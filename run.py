import subprocess, sys, time, os

last = 0
while True:
    current = os.path.getmtime("gui.py")
    if current != last:
        last = current
        if 'proc' in dir():
            proc.kill()
        proc = subprocess.Popen([sys.executable, "gui.py"])
    time.sleep(1)
    