import struct

class PIB:
    _pib = None
    
    def __init__(self, pib):
        assert(type(pib) == bytes)
        assert(len(pib) > 8) #Otherwise pib_size would break
        
        self._pib = pib
        
        assert(len(self) <= self.size())
        
    def pib(self):
        return self._pib
        
    def __len__(self):
        #Return the current stored length of the pib
        return len(self._pib)
    
    def __add__(self, newdata):
        assert(type(newdata) == bytes)
        return PIB(self._pib + newdata)
        
        
    def __getitem__(self, idx):
        return self._pib.__getitem__(idx)
        
    def size(self):
        """Returns the (complete) size of the PIB"""
        #Structure of PIB header (see open-plc-utils/pib/pib.h)
        return struct.unpack('<H',self._pib[4:6])[0]
        
    def is_complete(self):
        return len(self) == self.size()
        
    def is_valid(self):
        return (self.calc_cksum(self._pib) == 0)
        
    def master_get(self):
        return self._pib[0x2c8a:0x2c90]
        
    def master_replace(self, newmac_bytes):
        pib_data = self._pib[:8] + b'\x00\x00\x00\x00' + self._pib[12:0x2c8a]
        pib_data += newmac_bytes + self._pib[0x2c90:]
        
        new_pib = pib_data[:8] + struct.pack('<I',self.calc_cksum(pib_data)) + pib_data[12:]
        assert(self.calc_cksum(new_pib) == 0)
        
        return PIB(new_pib)
        
    def calc_cksum(self, data):
        cksum = 0
        for i in range(0, len(data), 4):
            cksum = (cksum ^ struct.unpack('<I', data[i:i+4])[0]) & (2**32-1)
            
        return (~cksum)& (2**32-1)
