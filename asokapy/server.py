#!/usr/bin/python3

import threading
import socket
import os
import select
import time
import struct

from configparser import ConfigParser

from asokapy.device import Device

class Server(threading.Thread):
    #Configparser of current config file
    _config = None
    #File name of config file
    _config_file = None
    
    #Interface
    _interface = None
    #Mac address to use (interface mac)
    _interface_mac = None
    #Mac address converted to bytes (to use in ethernet packets)
    _interface_mac_bytes = None
    
    #Which user to switch to
    _uid = None
    _gid = None
    
    #File handle of data log file
    _datalog = None
    
    #RAW socket
    _sock = None
    
    #(released during select)
    _lock_status = None
    #(released only during the transition of iteration in main loop
    _lock_config = None
    
    #Do we want to continue execution (set to False to abort)
    _continue = False
    
    #Timestamp of the last tick execution?
    _last_tick = None
    #Interval between two ticks
    _tick_interval = 1
    
    #Map: <mac address as bytes> => Device
    _devices = {}
    #List of devices mac address
    _devices_list = []
    
    def __init__(self, config_file):
        threading.Thread.__init__(self)
        
        #Basic initialization
        self._config_file = config_file
        self._continue = True
        self._last_tick = 0
        self._lock_config = threading.RLock()
        self._lock_status = threading.RLock()
        
        #Will be populated by reload
        self._devices = {}
        self._devices_list = []
        
        #(Re)load config and start thread
        self.reload()
        self.start()
        
    def run(self):
        try:
            while True:
                self._lock_config.acquire()
                #We cannot use the while condition, because we may have
                #problems with the socket (if configuration failed)
                if not self._continue:
                    break
                    
                try:
                    #Compute the time to wait in select
                    select_delay = max(0, self._tick_interval - (time.time() - self._last_tick))
                    
                    #Select (only one socket)
                    sockr,sockw,socke = select.select([self._sock], [], [], select_delay)
                    
                    #Now we protect the status
                    self._lock_status.acquire()
                    try:
                        #Read packets if needed
                        if self._sock in sockr:
                            r = self._sock.recv(2048)
                            self._handle_packet(r)
                        
                        #Run ticks if needed
                        if self._tick_interval <= (time.time() - self._last_tick):
                            self._last_tick = time.time()
                            self._handle_tick()
                            
                    finally:
                        #We're done with status modification
                        self._lock_status.release()
                        
                finally:
                    #Give a chance to reload configuration
                    self._lock_config.release()
                    time.sleep(0.05) #Allow signals to be treated
        finally:
            #If we exit the main loop, obviously we're not running
            self._continue = False
    
    def stop(self):
        """Stop the main loop"""
        self._continue = False
        
    def is_running(self):
        """Is the server running?"""
        return self._continue
             
    def _reload(self):
        """Reload configuration"""
        self._config = ConfigParser()
        self._config.read([self._config_file])
        
        if self._interface != self._config.get('master','interface'):
            if self._sock is not None:
                self._sock.close()
                self._sock = None
            
            self._interface = self._config.get('master','interface')
            self._sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
            self._sock.bind((self._interface, 0))
            
        self._interface_mac = self._config.get('master','mac')
        self._interface_mac_bytes = self._to_bytes(self._interface_mac)
        
        self._uid = self._config.getint('master','uid', fallback = None)
        self._gid = self._config.getint('master','gid', fallback = None)
        
        if self._gid is not None:
            os.setgid(self._gid)
            os.setegid(self._gid)
            
        if self._uid is not None:
            os.setuid(self._uid)
            os.seteuid(self._uid)
        
        if self._datalog is not None:
            self._datalog.close()
            self._datalog = None
        
        datalogfilename = self._config.get('master','datalog', fallback = None)
        if datalogfilename is not None:
            self._datalog = open(datalogfilename, 'a')


            
        new_devices_list = [x for x in self._config.sections() if ':' in x]
        new_devices_set = set(new_devices_list)
        old_devices_set = set(self._devices_list)
        
        devices_to_add = new_devices_set.difference(old_devices_set)
        devices_to_remove = old_devices_set.difference(new_devices_set)
        
        for d in devices_to_add:
            self._devices[self._to_bytes(d)] = Device(self, d)
            
        for d in devices_to_remove:
            del self._devices[self._to_bytes(d)]
            
        for d in new_devices_list:
            self._devices[self._to_bytes(d)].update_config(dict(self._config.items(d)))
        
        self._devices_list = new_devices_list
            
    def reload(self):
        """Reload configuration from file. Returns False if failed, and stops the server"""
        self._lock_config.acquire()
        try:
            return self._reload()
        except:
            self._continue = False
            return False
        finally:
            self._lock_config.release()
            
    def _handle_tick(self):
        """Send tick event to each device"""
        for d in self._devices.values():
            d.tick()
    
    def _handle_packet(self, recvdata):
        """Handle an incoming ethernet packet, and forward it to the
        correct device"""
        if recvdata[0:6] != self._interface_mac_bytes:
            #Not for me
            return False
        if recvdata[6:12] not in self._devices:
            #Not from a known device
            return False
            
        device = self._devices[recvdata[6:12]]
        
        if recvdata[12:14] == b'\x88\xe1':
            #HomePlugAV
            if recvdata[14:15] != b'\x00': #reserved
                #Bad reserved field
                return False
            if recvdata[17:20] != b'\x00\xb0\x52':
                #Bad MME OUI
                return False
            
            action = struct.unpack('<H',recvdata[15:17])[0]
            r = device.packet_homeplug(action, recvdata[20:])
        else:
            r = device.packet_ether(recvdata[12:])
            
        device.tick()
        return r
        
    def _send_to_device(self, device, msg):
        """Send msg to device, as a raw ethernet packet (mac addresses are added)"""
        self._lock_status.acquire()
        try:
            self._sock.send(self._to_bytes(device.remote_mac) + self._to_bytes(self._interface_mac) + msg)
        finally:
            self._lock_status.release()
            
    def _device_on(self, dev_mac):
        dev_mac_bytes = self._to_bytes(dev_mac)
        if dev_mac_bytes not in self._devices:
            raise ValueError("Invalid device {0}!".format(dev_mac))
        
        self._devices[dev_mac_bytes].on()
        
    def device_on(self, dev_mac):
        """Turn on device identified by dev_mac"""
        self._lock_status.acquire()
        try:
            return self._device_on(dev_mac)
        finally:
            self._lock_status.release()
            
    def _device_off(self, dev_mac):
        dev_mac_bytes = self._to_bytes(dev_mac)
        if dev_mac_bytes not in self._devices:
            raise ValueError("Invalid device {0}!".format(dev_mac))
        
        self._devices[dev_mac_bytes].off()
        
    def device_off(self, dev_mac):
        """Turn off device identified by dev_mac"""
        self._lock_status.acquire()
        try:
            return self._device_off(dev_mac)
        finally:
            self._lock_status.release()
        
    def _to_bytes(self, v):
        """Convert a colon separated string of hex-bytes into bytes"""
        assert(type(v) == str)
        return bytes([int(x,16) for x in v.split(':')])
        
    def _to_comma_separated(self, b):
        """Convert bytes to a colon separated string of hex-bytes"""
        assert(type(v) == bytes)
        return ":".join(['{0:02x}'.format(x) for x in b])
        
    def report_data(self, device, is_on, power):
        fields = ['{0:1.2f}'.format(time.time()),device.remote_mac]
        fields.append({True:'1',False:'0',None:''}[is_on])
        if power is None:
            fields.append('')
        else:
            fields.append('{0:1.1f}'.format(power))
        
        self._datalog.write('\t'.join(fields)+"\n")
        self._datalog.flush()
        
    def _device_info(self, dev_mac):
        dev_mac_bytes = self._to_bytes(dev_mac)
        if dev_mac_bytes not in self._devices:
            raise ValueError("Invalid device {0}!".format(dev_mac))
        
        dev = self._devices[dev_mac_bytes]
        return {'power': dev.device_power, 'is_on': dev.device_is_on, 'alias': dev.alias}
        
    def device_info(self, dev_mac):
        """Get info from device"""
        self._lock_status.acquire()
        try:
            return self._device_info(dev_mac)
        finally:
            self._lock_status.release()
        
        
        
if __name__ == '__main__':
    import sys
    import signal
    
    
    s = Server(sys.argv[1])
    
    def sighandler(signum, frame):
        if signum in (signal.SIGINT, signal.SIGTERM):
            s.stop()
        elif signum == signal.SIGHUP:
            s.reload()
            
    signal.signal(signal.SIGINT, sighandler)
    signal.signal(signal.SIGTERM, sighandler)
    signal.signal(signal.SIGHUP, sighandler)
    
    try:
        t=0
        while s.is_running():
            t+=1
            time.sleep(1)
    except:
        s.stop()
        raise
