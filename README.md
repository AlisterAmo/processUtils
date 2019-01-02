# processUtils
processUtils: a wrapper that enhances your experience with Python Subprocess.Popen and workarounds some of its limitations (Python 2.7)  

If you are like me, you probably write python programs that need to repeatedly launch several party tools or system utilities, and then you parse the output. But this process often becomes more difficult than expected, specially because the subprocess module shipped in python gives little or no control of the Popen instances once they are launched.  
  
## Features
This module provides:

* Easy method to prepare your Popen command with nothing more than a simple string. Like when you use `shell=True`, but without using such a dangerous parameter. 
* A complete, iterable, autoupdated data structure with all the relevant information per each Popen instance:
** Original string of the command launched and parameters (string)
** Running/finished status (boolean)
** Return code (if finished)
** Did it return an error? (boolean)
** Was it killed by a signal? (boolean)
** Type of signal that killed the process (int)
** Time of launch / finish (time.time() format)
** Working directory used in the Popen instance
** And the most useful and desired: NON BLOCKING READ of stdout/stderr, in the form of convenient string attributes that you can check at any point in your program

## Example

```
import time
from processUtils import *  
programlauncher = SubprocessManager()
programlauncher.launch('/full/path/to/external/tool -o "complex set" "of parameters" "between quotes"')
# this will show the stdout so far, without risk of deadlocks :) 
print "Output of the program launched:\n" + programlauncher.last.stdout  # Note the convenience shortcut programlauncher.last, always pointing to the last program launched so its easier to handle
time.sleep(60)  # we pause a minute
if programlauncher.last.running:
  print "The program is still running after one minute, we will ask it to terminate..."
  programlauncher.last.terminate()  # use .terminate() to send a terminate signal
else:
  howmuchtimeago = time.time() - programlauncher.last.finishedat  # 
  print "The program was terminated {} seconds ago.".format(howmuchtimeago)  
```

I hope you get the idea, but feel free to inspect the self explanatory contents of the code. I have not written a better documentation yet and the module is still in process of changes and incorporation of new features, your suggestions and commits are more than welcome.

