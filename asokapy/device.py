import collections
import weakref
import struct
import time
from asokapy.pib import PIB

#Device is a state machine, which states are defined here.
#See doc/device_states.dot for transitions.

#Probing state (only ethernet)
DSProbing = collections.namedtuple('DSProbing', ['last_sent', 'num_sent'])
#Probing state (ethernet + HomePlugAV)
DSProbingHP = collections.namedtuple('DSProbingHP', ['last_sent'])

#Read PIB from device
DSReadPIB = collections.namedtuple('DSReadPIB', ['start_time','last_sent', 'pib'])
#Write PIB to device
DSWritePIB = collections.namedtuple('DSWritePIB', ['start_time','last_sent', 'pib_current_offset', 'pib'])
#Write PIB to NVM (only one packet)
DSWritePIBToNVM = collections.namedtuple('DSWritePIBToNVM', ['start_time','last_sent'])
#Running state
DSRunning = collections.namedtuple('DSRunning', ['last_sent','last_received'])

class Device:
    #weakref to server
    server = None
    
    #config
    alias = None
    interval = None
    remote_mac = None
    
    #state
    state = None
    
    #Do we want to turn it on/off
    want_on = None
    
    #Status
    device_type = None
    device_version = None
    device_unknown_tuple = None
    
    device_power = None
    device_is_on = None
    
    #Config
    probe_delay = 10 #delay between probe in DSProbing state
    max_probing_tries = 5 #Number of probes to send
    pib_chunk = 1024 #Chunk size of PIB read
    pib_abort_time = 20 #Timeout (s) in DS*PIB* states
    running_abort_time = 20 #Timeout (s) in DSRunning state
    
    def __init__(self, server, remote_mac):
        self.server = weakref.proxy(server)
        self.remote_mac = remote_mac
        self.reset_state()
        
    def reset_state(self):
        self.state = DSProbing(last_sent = 0, num_sent = 0)
        #No indication about power
        self.device_power = None
        self.device_is_on = None
        
    def update_config(self, values):
        if 'interval' in values:
            self.interval = int(values['interval'])
        else:
            self.interval = None
            
        if 'alias' in values:
            self.alias = values['alias']
        else:
            self.alias = None
            
    def tick(self):
        if self.state.__class__ == DSProbing:
            #Send a probe every probe_delay
            if self.state.last_sent < time.time() - self.probe_delay:
                self.send_ether_probe()
                
                #Maybe we're not the master, so we need to probe homeplug too
                if self.state.num_sent >= self.max_probing_tries:
                    self.state = DSProbingHP(last_sent = time.time())
                else:
                    self.state = DSProbing(last_sent = time.time(), num_sent = self.state.num_sent + 1)
            return
        
        if self.state.__class__ == DSProbingHP:
            #Send ethernet and HomePlugAV probes every probe_delay
            if self.state.last_sent < time.time() - self.probe_delay:
                self.send_ether_probe()
                self.send_hp_probe()
                self.state = DSProbingHP(last_sent = time.time())
            return
        
        if self.state.__class__ == DSRunning:
            #If we want to switch on/off, and it doesn't correspond to current state
            if self.device_is_on != self.want_on and self.want_on is not None:
                #Send correct packet
                if self.want_on:
                    self.send_ether_on()
                else:
                    self.send_ether_off()
                #It seems to be best to wait a little before doing another query
                self.state = DSRunning(last_sent = time.time(), last_received = self.state.last_received)
                return
            
            #Do we want to query at some fixed interval?
            if self.interval is not None:
                #Too long without receiving packet: abort
                if time.time() - self.state.last_received > self.running_abort_time:
                    self.reset_state()
                    return
                
                #Send a probe every interval
                if self.state.last_sent < time.time() - self.interval:
                    self.send_ether_probe()
                    self.state = DSRunning(last_sent = time.time(), last_received = self.state.last_received)
            return
            
        #Handle timeout in PIB state
        if self.state.__class__ in (DSReadPIB, DSWritePIB, DSWritePIBToNVM):
            if self.state.start_time < time.time() - self.pib_abort_time:
                self.reset_state()
                return
            
        #In the 3 PIB states, we send a packet every probe_delay if we
        #don't get an answer. (maybe the packet was lost?)
        if self.state.__class__ == DSReadPIB:
            if self.state.last_sent < time.time() - self.probe_delay:
                self.send_hp_read_pib(len(self.state.pib), min(self.state.pib.size() - len(self.state.pib),self.pib_chunk))
                self.state = DSReadPIB(start_time=self.state.start_time,last_sent=time.time(),pib=self.state.pib)
            return
            
        if self.state.__class__ == DSWritePIB:
            if self.state.last_sent < time.time() - self.probe_delay:
                self.send_hp_write_pib()
                self.state = DSWritePIB(start_time=self.state.start_time,last_sent=time.time(),pib_current_offset=self.state.pib_current_offset,pib=self.state.pib)
            return
            
        if self.state.__class__ == DSWritePIBToNVM:
            if self.state.last_sent < time.time() - self.probe_delay:
                self.send_hp_write_pib_to_nvm()
                self.state = DSWritePIBToNVM(start_time=self.state.start_time,last_sent=time.time())
            return
            
        #Unknown state... shouldn't happen
        assert False
        
    def packet_homeplug(self, action, data):
        #Do we expect HomePlugAV packets?
        if self.state.__class__ not in (DSProbingHP, DSReadPIB, DSWritePIB, DSWritePIBToNVM):
            return False
            
        #We expect Device Read Confirmation in these states
        if self.state.__class__ in (DSProbingHP, DSReadPIB) and action != 0xa025:
            return False
        
        #We expect Device Write Confirmation in DSWritePIB state
        if self.state.__class__ in (DSWritePIB, ) and action != 0xa021:
            return False
            
        #We expect Device Write to NVM Confirmation in DSWritePIBToNVM state
        if self.state.__class__ in (DSWritePIBToNVM, ) and action != 0xa029:
            return False
            
        if struct.unpack('<B',data[0:1])[0] != 0: #Not Success
            return False
            
        #PIB written successfully to NVM
        if self.state.__class__ == DSWritePIBToNVM:
            self.reset_state()
            return True
            
        if self.state.__class__ == DSWritePIB:
            #Last chunk?
            if self.state.pib_current_offset + self.pib_chunk >= len(self.state.pib):
                #Write to NVM
                self.state = DSWritePIBToNVM(start_time = time.time(), last_sent = 0)
                return True
                
            #Next chunk
            self.state = DSWritePIB(start_time = self.state.start_time, last_sent = 0, pib_current_offset = self.state.pib_current_offset + self.pib_chunk, pib = self.state.pib)
            return True
            
        if self.state.__class__ in (DSProbingHP, DSReadPIB):
            status, module, length, offset = struct.unpack('<BxxxBxHI',data[:12])
            
            if module != 0x2: #Not PIB
                return False
            
            if self.calc_cksum(data[12:]) != 0: #Wrong checksum
                return False
                
            #Get "real" data
            data = data[16:16+length]
                
            #Probing, we got the beginning of the PIB
            if self.state.__class__ == DSProbingHP:
                if offset != 0: #In state probing, and not the beginning of the PIB
                    return False
                
                #Read PIB
                self.state = DSReadPIB(start_time = time.time(), last_sent = 0, pib=PIB(data))
                
            elif self.state.__class__ == DSReadPIB:
                #Correct offset?
                if offset != len(self.state.pib):
                    return
                
                #Create a new PIB with the new data appended
                newpib = self.state.pib + data
                
                #PIB is downloaded
                if newpib.is_complete():
                    if not newpib.is_valid():
                        #Wrong checksum, reset everything
                        self.reset_state()
                        return
                    
                    master_mac_pib = newpib.master_get()
                    master_mac_server = self.server._interface_mac_bytes
                    #Do we have the correct server?
                    #LF: disabled: sometimes, the devices doesn't respond?
                    if master_mac_pib == master_mac_server:
                        #Address is already correct, abort!
                        self.reset_state()
                        return
                        
                    #Ok, write the PIB with the new server
                    self.state = DSWritePIB(start_time = time.time(), last_sent = 0, pib_current_offset=0, pib=newpib.master_replace(master_mac_server))
                    
                else:
                    #PIB is not complete, read next packet
                    self.state = DSReadPIB(start_time = self.state.start_time, last_sent = 0, pib=newpib)
                    
                    
                
        
        
    def packet_ether(self, data):
        if len(data) < 4:
            return False
            
        if self.state.__class__ not in (DSProbing, DSProbingHP, DSRunning):
            return False
            
        #Packet consists of multiple 64-bytes chunks, length is data[1].
        length = struct.unpack('<B', data[1:2])[0]
        if len(data[2:])!=length:
            return False
            
        if length % 64 != 0:
            #Guess?
            return False
            
        #Remove the header
        packet_data = data[2:]
        
        #For each 64-bytes chunk
        for msg_start in range(0, length, 64):
            mdata = packet_data[msg_start:msg_start+64]
            mdata_function = mdata[0]
            mdata_length = mdata[1]
            mdata_message = mdata[2:2+mdata_length]
            
            #1 = power information
            if mdata_function == 1:
                self.receive_powerdata(mdata_message.decode('ascii').strip())
                self.state = DSRunning(last_sent = self.state.last_sent, last_received = time.time())
                continue
                
            #9 = reply to on/off
            #12 = unsollicited
            if mdata_function in (9, 12):
                #On/Off information (in first byte)
                assert(mdata_length==1)
                
                mdata_state = mdata_message[0]
                
                if mdata_state == 1:
                    self.receive_is_on()
                elif mdata_state == 0:
                    self.receive_is_off()
                self.state = DSRunning(last_sent = self.state.last_sent, last_received = time.time())
                continue
            
            #Unknown ethernet packet... it would be better to log it
            hexdata = ":".join(['{0:02X}'.format(x) for x in data])
            print(self.remote_mac, "ether", hexdata)
        
    def send_ether_probe(self):
        msg = b'\x00\x40' + b'\x00\x00\x00' + b'\x00'*60 + b'\x01'
        self.server._send_to_device(self, msg)
        
    def send_ether_on(self):
        self.device_is_on = None
        msg = b'\x00\x40' + b'\x08\x01\x01' + b'\x00'*60 + b'\x00'
        self.server._send_to_device(self, msg)
        
    def send_ether_off(self):
        self.device_is_on = None
        msg = b'\x00\x40' + b'\x08\x01\x00' + b'\x00'*60 + b'\x01'
        self.server._send_to_device(self, msg)
        
    def send_hp_probe(self):
        self.send_hp_read_pib(0, self.pib_chunk)
        
    def send_hp_read_pib(self, offset, length):
        msg = b'\x88\xe1' #HomePlug AV
        msg += b'\x00' #v1.0
        msg += struct.pack('<H',0xa024) #Read Module Data Request
        msg += b'\x00\xb0\x52' #Vendor MME OUI
        msg += b'\x02' #Module ID: PIB
        msg += b'\x00' #Reserved
        msg += struct.pack('<H',length)
        msg += struct.pack('<I',offset)
        
        self.server._send_to_device(self, msg)
        
    def send_hp_write_pib(self):
        assert(self.state.__class__ == DSWritePIB)
        data = self.state.pib[self.state.pib_current_offset:self.state.pib_current_offset+self.pib_chunk]
        
        msg = b'\x88\xe1' #HomePlug AV
        msg += b'\x00' #v1.0
        msg += struct.pack('<H',0xa020) #Write Module Data Request
        msg += b'\x00\xb0\x52' #Vendor MME OUI
        msg += b'\x02' #Module ID: PIB
        msg += b'\x00' #Reserved
        msg += struct.pack('<H',len(data))
        msg += struct.pack('<I',self.state.pib_current_offset)
        
        msg += struct.pack('<I',self.calc_cksum(data))
        msg += data
        
        self.server._send_to_device(self, msg)
        
    def send_hp_write_pib_to_nvm(self):
        assert(self.state.__class__ == DSWritePIBToNVM)

        msg = b'\x88\xe1' #HomePlug AV
        msg += b'\x00' #v1.0
        msg += struct.pack('<H',0xa028) #Write Module Data to NVM Request
        msg += b'\x00\xb0\x52' #Vendor MME OUI
        msg += b'\x02' #Module ID: PIB
        
        self.server._send_to_device(self, msg)
        
    def receive_powerdata(self, data):
        parts = data.split(';')
        assert(parts[0] in ('2','3'))
        assert(parts[3] in ('0','1'))
        
        device_type = parts[0]
        device_is_on = (parts[3] == '1')
        device_power = float(parts[4])
        
        if device_type == '2':
            #Blue device
            device_unknown_tuple = (parts[1], parts[5], parts[6])
            device_version = (parts[2], parts[7])
            pass
        elif device_type == '3':
            #White device
            device_unknown_tuple = (parts[1])
            device_version = (parts[2], )
            pass
            
        if self.device_unknown_tuple is None:
            self.device_unknown_tuple = device_unknown_tuple
        if self.device_version is None:
            self.device_version = device_version
        if self.device_type is None:
            self.device_type = device_type
            
        assert(self.device_unknown_tuple == device_unknown_tuple)
        assert(self.device_version == device_version)
        assert(self.device_type == device_type)
        
        self.device_power = device_power
        self.device_is_on = device_is_on
         
        self.server.report_data(self, self.device_is_on, self.device_power)
        
    def receive_is_on(self):
        #Set want_on to null if we reached the target state
        if self.want_on is not None:
            if self.want_on:
                self.want_on = None
                
        self.device_is_on = True
        
        self.server.report_data(self, self.device_is_on, None)
        
    def receive_is_off(self):
        #Set want_on to null if we reached the target state
        if self.want_on is not None:
            if not self.want_on:
                self.want_on = None
                
        self.device_is_on = False
        self.server.report_data(self, self.device_is_on, None)
        

    def on(self):
        self.want_on = True
        return
        
    def off(self):
        self.want_on = False
        return
        
    def calc_cksum(self, data):
        cksum = 0
        for i in range(0, len(data), 4):
            cksum = (cksum ^ struct.unpack('<I', data[i:i+4])[0]) & (2**32-1)
            
        return (~cksum)& (2**32-1)
