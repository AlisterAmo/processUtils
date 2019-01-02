# -*- coding: utf-8 -*-
from subprocess import Popen,PIPE, STDOUT
from Queue import Queue, Empty
from threading import Thread
import shlex
import logging
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
            self.stdout = ""  # clear text, self updating contents of stdout
            self.stderr = ""  # clear text, self updating contents of stderr
            self._popeninstance = Popen(self._parsedcommand, bufsize=-1, preexec_fn=os.setsid, close_fds=True, stderr=PIPE, stdout=PIPE, stdin=PIPE, cwd=self.workingdirectory, universal_newlines=True)
            self.pid = self._popeninstance.pid
            # stdout/stderr queue setup
            self._stderrq = Queue()
            self._stderrt = Thread(target=self._enqueue_output, args=(self._popeninstance.stderr, self._stderrq))
            self._stderrt.daemon = True  # thread dies with the program
            self._stderrt.start()

            self._stdoutq = Queue()
            self._stdoutt = Thread(target=self._enqueue_output, args=(self._popeninstance.stdout, self._stdoutq))
            self._stdoutt.daemon = True  # thread dies with the program
            self._stdoutt.start()
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
                self.cleanupafterfinished()
            if not self.running:
                self.finishedat = (time.time() if not self.running else None)
                self.didreturnerror = (self.returncode != 0)
                if self.didreturnerror:
                    self.waskilledbysignal = (self.returncode < 0)
                    self.signalthatkilledprocess = abs(self.returncode)
            ## flush stdout/stderr queues without blocking
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

            returnvalue = {'running': self.running,
                           'finishedat': self.finishedat,
                           'returncode': self.returncode,
                           'didreturnerror': self.didreturnerror,
                           'waskilledbysignal': self.waskilledbysignal,
                           'signalthatkilledtheprocess': self.signalthatkilledprocess,
                           'stdout': self.stdout,
                           'stderr': self.stderr}
            log.debug('Stats de {} despues de _update:\n{}'.format(self, returnvalue))
            return returnvalue


        @staticmethod
        def _enqueue_output(output, queue):
            for line in iter(output.readline, b''):
                queue.put(line)
            output.close()


        def cleanupafterfinished(self):
            pass


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


# Algunas utilidades que dejamos fuera de los objetos porque pueden ser de utilidad general

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
    '''
    cmd = "tail -n 15 -f /var/log/kern.log"  # simple código de testing que se ejecuta al ser llamados directamente
    # lista de señales con las que queremos experimentar y cantidad de veces de la prueba
    listofsignalstosend = [signal.SIGSTOP] + [signal.SIGTERM] * 7 + [signal.SIGKILL] * 7
    i = 0
    for i in range(len(listofsignalstosend)):  # crearemos un proceso para cada test de señal a enviar
        log.debug('==================================')
        log.debug('Lanzamiento de proceso: {}'.format(i+1))
        testingmanagedsubprocess.launch(cmd)
    log.debug('')
    log.debug("Pausa antes de enviar señales a los procesos lanzados...")
    log.debug('')
    time.sleep(2)
    # ahora, a enviar las señales para completar los tests
    i = 0
    for i in range(len(testingmanagedsubprocess.managedsubprocesses)):
        # para iterar por los ManagedSubprocess, en este punto se espera que sea la misma cantidad que listofsignalstosend
        currentmanagedsubprocess = testingmanagedsubprocess.managedsubprocesses[i]
        tipeofsignal = listofsignalstosend.pop()
        log.debug('==================================')
        log.debug('Test de envío de señal {}. ManagedSubprocess index: {}. Señal: {}'.format(i+1, currentmanagedsubprocess, tipeofsignal))
        log.debug('Comprobando si el PID {} sigue activo...'.format(currentmanagedsubprocess.pid))
        if currentmanagedsubprocess.running:
            if tipeofsignal == signal.SIGTERM:
                log.debug('Llamando al método terminate del ManagedSubprocess..')
                returnvalue = testingmanagedsubprocess.terminate(currentmanagedsubprocess)
            elif tipeofsignal == signal.SIGKILL:
                log.debug('Llamando al método kill del ManagedSubprocess..')
                returnvalue = testingmanagedsubprocess.kill(currentmanagedsubprocess)
            else:
                log.debug('Llamando al método send_signal({}) del ManagedSubprocess..'.format(tipeofsignal))
                returnvalue = testingmanagedsubprocess.send_signal(currentmanagedsubprocess, tipeofsignal)
        else:
            log.warn('Proceso no aparece como activo. Ignorando envío de señal.')
    log.debug('==================================')
    log.debug('Stats al final del test: {}'.format(testingmanagedsubprocess.stats))
    '''
    log.info('Test de stdout. Lanzando proceso ls...')
    test_process = testingmanagedsubprocess.launch("ls")
    print test_process.stdout
    time.sleep(1)
    log.info('Test de stderr. Lanzando proceso cd con parámetros erróneos...')
    test_process = testingmanagedsubprocess.launch("ls carpetaquenoexiste")
    print test_process.stderr

    log.info('Test de send_input. Lanzando proceso que pide entrada por stdin...')

    # stdout_test_process.send_input_lf('lolazo')
    # while test_process.running:
    test_process = testingmanagedsubprocess.launch("./stdintest.sh")
    log.info("Captando stdout antes de enviar datos a stdin:")
    log.info(test_process.stdout)
    log.info("Enviando datos a stdin:")
    test_process.send_input_lf("this is a testing input sent to the waiting process")
    time.sleep(2)
    a = test_process._update()
    log.info(test_process.stdout)
    log.info(a)

    # log.info('Mostrando stderr: \n{}'.format(stdout_test_process.stderr))

    # log.debug('Cleaning up...')
    # testingmanagedsubprocess.killall
