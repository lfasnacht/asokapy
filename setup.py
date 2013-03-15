import sys

if sys.version < "3":
    print("Python 3 is required.")
    sys.exit(0)
    
from distutils.core import setup

setup(name='asokapy',
      version='1.0',
      packages=['asokapy'],
      license = "GNU GPLv3",
      )
