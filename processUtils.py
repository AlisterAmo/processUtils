# -*- coding: utf-8 -*-
from subprocess import Popen,PIPE
# from subprocess32 import Popen,PIPE,check_output
from Queue import Queue, Empty
import threading
import shlex
import time
import os
import signal
import errno
from logengine import *
# logging stuff beforehand
log = addlogengine("PsMgr")
log.setLevel(logging.WARNING)

class SubprocessManager:
    def __init__(self):
        self.managedsubprocesses = []  # un diccionario de los objetos ManagedProcess que vamos lanzando, para poderlos controlar mejor
        self.stats = []


    def _updatemanagedsubprocess(self, managedsubprocess):
        log.debug("Calling update for specific ManagedSubprocess {}".format(managedsubprocess))
        managedsubprocess_stats = managedsubprocess._update()
        self._updatestats(agressive=False)
        return managedsubprocess_stats


    def _updatestats(self, **kwargs):
        log.debug('Updating SubprocessManager stats...')
        tmp_stats_running = 0
        self.launched = len(self.managedsubprocesses)
        agressive = kwargs.get('agressive')
        if agressive:
            log.debug("Agressive update requested. Will request refresh for all ManagedSubprocess instances")
        else:
            log.debug("Soft update requested. Will trust the existent property values of ManagedSubprocess instances")
        for managedsubprocess in self.managedsubprocesses:
            if agressive:
                log.debug("Refreshing {}".format(managedsubprocess))
                managedsubprocess._update()
            if managedsubprocess.running:
                log.debug('{} reports itself RUNNING'.format(managedsubprocess))
                tmp_stats_running += 1
            else:
                log.debug('{} reports itself FINISHED'.format(managedsubprocess))
        # log.debug("STATS. Launched:{}, Running:{}".format(stats_launched, stats_running))
        self.running = tmp_stats_running
        self.stats = {'launched': self.launched,
                      'running': self.running}
        log.debug('Stats of Process Manager: {}'.format(self.stats))
        return self.stats

    def launch(self, cmd, **kwargs):
        try:
            newmanagedprocess = self.ManagedSubprocess(cmd, **kwargs)
        except:
            # todo: a medida que vayan saliendo posibles errores de estos métodos, ir manejándolos aquí
            raise
        else:
            log.debug("New process launched with cmd={} and pid={}.".format(
                newmanagedprocess.command, newmanagedprocess.pid))
            # log.debug("Adding new process to manager: {}".format(newsubprocesstoadd))
            self.managedsubprocesses.append(newmanagedprocess)
            self.lastlaunched = newmanagedprocess
            self._updatestats()
            return newmanagedprocess


    def send_signal(self, managedsubprocess, signaltosend):
        log.debug('Sending signal {} to {}'.format(signaltosend, managedsubprocess))
        returnvalue = managedsubprocess.send_signal(signaltosend)
        # self._updatemanagedsubprocess(managedsubprocess)
        self._updatestats()
        return returnvalue

    def kill(self, managedsubprocess):
        returnvalue = self.send_signal(managedsubprocess, signal.SIGKILL)
        managedsubprocess._popeninstance.wait()
        return returnvalue

    def terminate(self, managedsubprocess):
        returnvalue = self.send_signal(managedsubprocess, signal.SIGTERM)
        managedsubprocess._popeninstance.wait()
        return returnvalue

    class AsynchronousFileReader(threading.Thread):
        '''
        Helper class to implement asynchronous reading of a file
        in a separate thread. Pushes read lines on a queue to
        be consumed in another thread.
        '''

        def __init__(self, fd, queue):
            assert isinstance(queue, Queue)
            assert callable(fd.readline)
            threading.Thread.__init__(self)
            self._fd = fd
            self._queue = queue

        def run(self):
            '''The body of the tread: read lines and put them on the queue.'''
            for line in iter(self._fd.readline, ''):
                self._queue.put(line)

        def eof(self):
            '''Check whether there is no more content to expect.'''
            return not self.is_alive() and self._queue.empty()

    class ManagedSubprocess:
        def __init__(self, command, **kwargs):
            self.command = command
            self._parsedcommand = shlex.split(command)
            self.running = True
            self.returncode = None
            self.didreturnerror = None
            self.waskilledbysignal = False
            self.signalthatkilledprocess = None
            self.workingdirectory = kwargs.get('cwd')
            self.finishedat = None
            self.startedat = time.time()
            self._stdout = ""  # clear text, self updating contents of stdout
            self._stderr = ""  # clear text, self updating contents of stderr
            self._popeninstance = Popen(self._parsedcommand, bufsize=-1, preexec_fn=os.setsid, close_fds=True, stderr=PIPE, stdout=PIPE, stdin=PIPE, cwd=self.workingdirectory, universal_newlines=True)
            self.pid = self._popeninstance.pid
            
            # Launch the asynchronous readers of the process' stdout and stderr.
            self._stdoutq = Queue()
            self._stdout_reader = SubprocessManager.AsynchronousFileReader(self._popeninstance.stdout, self._stdoutq)
            self._stdout_reader.start()

            self._stderrq = Queue()
            self._stderr_reader = SubprocessManager.AsynchronousFileReader(self._popeninstance.stderr, self._stderrq)
            self._stderr_reader.start()

            # force a first update of subprocess stats
            self._update()


        def _update(self):
            log.debug('ManagedSubprocess._update')
            time.sleep(0.1)  # this delay is CRITICAL to get more accurate stats, give the process some time to be polled!
            self.returncode = self._popeninstance.poll()  # poll antes de nada, para actualizar la instancia Popen
            if self.returncode == None:
                self.running = True
            else:
                self.running = False
            if not self.running:
                self.finishedat = (time.time() if not self.running else None)
                self.didreturnerror = (self.returncode != 0)
                if self.didreturnerror:
                    self.waskilledbysignal = (self.returncode < 0)
                    self.signalthatkilledprocess = abs(self.returncode)
            '''
            ## flush stdout/stderr queues without blocking (old version)
            queues = {self._stdoutq, self._stderrq}
            for queue in queues:
                queue_not_empty = True
                output = ""
                try:
                    while queue_not_empty:
                        line = queue.get_nowait()
                        output += line
                        # sys.stdout.write("Getting line: {}".format(line))
                except Empty:
                    queue_not_empty = False
                    # print 'Polled queue is now empty: {}'.format(self._stdoutq)
                if queue == self._stdoutq:
                    self.stdout += output
                elif queue == self._stderrq:
                    self.stderr += output
            '''
            # Check the queues if we received some output (until there is nothing more to get).
            # Show what we received from standard output.
            while not self._stdoutq.empty():
                line = self._stdoutq.get()
                self._stdout += line
                log.debug('Received line on standard output: ' + repr(line))

            # Show what we received from standard error.
            while not self._stderrq.empty():
                line = self._stderrq.get()
                self._stderr += line
                log.debug('Received line on standard error: ' + repr(line))

            if not self.running:  # cleanup after process is finished. the latest in the flow, the better
                self._stdout_reader.join()
                self._stderr_reader.join()
                self._popeninstance.stderr.close()
                self._popeninstance.stdout.close()

            returnvalue = {'running': self.running,
                           'finishedat': self.finishedat,
                           'returncode': self.returncode,
                           'didreturnerror': self.didreturnerror,
                           'waskilledbysignal': self.waskilledbysignal,
                           'signalthatkilledtheprocess': self.signalthatkilledprocess,
                           'stdout': self._stdout,
                           'stderr': self._stderr}
            log.debug('Stats de {} despues de _update:\n{}'.format(self, returnvalue))
            return returnvalue

        @property
        def stdout(self):
            self._update()
            return self._stdout


        @property
        def stderr(self):
            self._update()
            return self._stderr


        def send_input(self, input):
            #  esto es una implementación parcial de communicate(), pero sin tocar stout ni stderr, solo stdin
            #  esto evita errores de concurrencia de E/S con el thread que tenemos para manejar stdout y stderr
            try:
                self._popeninstance.stdin.write(input)
            except IOError as e:
                if e.errno != errno.EPIPE and e.errno != errno.EINVAL:
                    raise
            self._popeninstance.stdin.close()
            self._popeninstance.wait()
            '''
            Desgraciadamente python nos obliga a experar y cerrar, esto solo se puede usar una vez
            en caso contrario se produce un deadlock y el proceso no avanza nunca
            es una limitación conocida de Python, todavia en proceso de mejora para Python3
            '''
            return self._update()

        def send_input_lf(self, input):
            input = input + "\n"
            return self.send_input(input)

        def send_signal(self, signaltosend):
            log.debug('{} received signal {}'.format(self, signaltosend))
            self._popeninstance.send_signal(signaltosend)
            returnvalue = self._update()
            log.debug('Updating stats for {} after sending signal...'.format(self))
            return returnvalue


def subprocess_alive(popeninstance):
    poll = popeninstance.poll()
    if poll == None:
        return True
    else:
        return False

def pid_exists(pid):
    import os
    import errno
    from subprocess import Popen
    """Check whether pid exists in the current process table."""
    if pid < 0:
        return False
    if pid == 0:
        # According to "man 2 kill" PID 0 refers to every process
        # in the process group of the calling process.
        # On certain systems 0 is a valid PID but we have no way
        # to know that in a portable fashion.
        raise ValueError('invalid PID 0')
    try:
        os.kill(pid, 0)
    except OSError as err:
        if err.errno == errno.ESRCH:
            # ESRCH == No such process
            exists = False
        elif err.errno == errno.EPERM:
            # EPERM clearly means there's a process to deny access to
            exists = True
        else:
            # According to "man 2 kill" possible error values are
            # (EINVAL, EPERM, ESRCH)
            raise
    else:
        exists = True
    if exists:
        return Popen.poll(pid)
    
    
## Some testing code

if __name__ == "__main__":
    log.debug("Testeando ManagedSubprocess")
    testingmanagedsubprocess = SubprocessManager()
    log.info('Testing. Launching stdoutgenerator.sh.')
    test_process = testingmanagedsubprocess.launch("yes testing")  # if you wanna test, call the module directly 
    i = 0
    while i < 100:
        i += 1
        print test_process.stdout  # this var is updated in near real time so you can access the stdout/stderr outputs ASAP 
        time.sleep(0.1)

