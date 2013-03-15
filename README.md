asokapy
=======

Python library and tools to use Asoka PlugLine PL7667-ETH &amp; PL7667-SW.

Requirements
------------

  * Python 3
  * Root access

Building asokapy
----------------

    git clone https://github.com/lfasnacht/asokapy.git
    cd asokapy
    sudo python3 setup.py install

Running asokapy
---------------

You have to create a configuration file (see doc/sample_config.ini).

After this, you can run asokapy interactive demo (as root, as it needs to bind a RAW ethernet device):

    python3 -m asokapy.interactive <your config file>

Contributing
------------

This software is provided under the GPLv3 license, and you're welcome to contribute. Please fork, and send pull requests.
