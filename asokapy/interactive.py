#!/usr/bin/python3

import sys

if __name__!='__main__':
    print("This file is not intended to be imported!")
    sys.exit(1)

from asokapy.server import Server
import curses

s = Server(sys.argv[1])

myscreen = curses.initscr()
curses.halfdelay(10)
curses.noecho()

while s.is_running():
    myscreen.clear()
    myscreen.border(0)
    
    dev_list = s._devices_list
    dev_info = dict([(dev, s.device_info(dev)) for dev in dev_list])
    max_alias_len = max([len(d['alias'])+3 for d in dev_info.values() if d['alias'] is not None] + [0])
    c = 0
    for dev in s._devices_list:
        c+=1
        di = dev_info[dev]
        myscreen.addstr(c+1, 2, "{0}".format(c))
        myscreen.addstr(c+1, 4, "{0}".format(dev))
        if di['alias'] is not None:
            myscreen.addstr(c+1, 22, "({0})".format(di['alias']))
        
        if di['power'] is not None:
            powerstr = "{0:1.1f} W".format(di['power'])
            powerstr = (8-len(powerstr))*" "+powerstr
            myscreen.addstr(c+1, 22 + max_alias_len, powerstr)
            
        if di['is_on'] is not None:
            if di['is_on']:
                myscreen.addstr(c+1, 31 + max_alias_len, '<ON>')
            else:
                myscreen.addstr(c+1, 31 + max_alias_len, '<OFF>')
    
    myscreen.refresh()
    action = myscreen.getch()
    
    #Escape, q
    if action in (113, 27):
        break
        
    if action >= 49 and action < 59:
        #Numeric
        dev_id = action - 49
        if dev_id < len(dev_list):
            dev = dev_list[dev_id]
            di = dev_info[dev]
            if di['is_on']:
                s.device_off(dev)
            else:
                s.device_on(dev)
    
    myscreen.addstr(10, 1, '<{0}>'.format(action))
        

s.stop()
curses.endwin()
