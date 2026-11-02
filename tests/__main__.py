"""Run the curated checks in isolated processes, with local setup smoke opt-in."""
import argparse
import os
import subprocess
import sys

from tests import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hardware', action='store_true', help='also read actual display and pointer state')
    args = parser.parse_args()
    modules = ['test_logic', 'test_resolutions', 'test_input_cleanup', 'test_recovery',
               'test_scripts', 'test_launcher', 'test_link']
    if args.hardware:
        modules.append('test_setup')
    scratch = ROOT / '.cache' / 'tests'
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', TEMP=str(scratch), TMP=str(scratch))
    logs, failures = [], []
    for name in modules:
        module = f'tests.{name}'
        print(f'Running {module}...', flush=True)
        result = subprocess.run([sys.executable, '-m', module], cwd=ROOT, env=env,
                                capture_output=True, text=True, encoding='utf-8', timeout=90)
        logs.append(f'{module} EXIT {result.returncode}\n{result.stdout}\n{result.stderr}')
        if result.returncode:
            failures.append(module)
            print(result.stdout + result.stderr)
        else:
            print(result.stdout.strip().splitlines()[-1])
    logfile = scratch / 'results.log'
    logfile.write_text('\n'.join(logs), encoding='utf-8')
    print(f'{len(modules) - len(failures)}/{len(modules)} checks passed. Log: {logfile}')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
