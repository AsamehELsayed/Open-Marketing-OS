"""Send synthetic local fixture setup to only the named dev050 Compose service."""
import argparse
import json
import subprocess
import re
from pathlib import Path
HERE = Path(__file__).resolve().parent

def local_secrets():
    return dict(line.split('=',1) for line in (HERE/'.env').read_text().splitlines() if line and not line.startswith('#'))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode',choices=['seed','channels','snapshot'])
    parser.add_argument('--receipt',type=Path)
    args = parser.parse_args()
    values = local_secrets()
    data = {'mode':args.mode, 'keys':{'a':values['DEV050_KEY_A'],'b':values['DEV050_KEY_B']}}
    source = (HERE/'seed.cjs').read_text().replace('__DEV050_INPUT__',json.dumps(data))
    result = subprocess.run(['docker','compose','-p','dev050','-f',str(HERE/'compose.json'),
        'exec','-T','postiz','node'],input=source,text=True,capture_output=True,timeout=30,cwd=HERE)
    if result.returncode:
        raise SystemExit('Local fixture operation failed; inspect private dev050 container logs.')
    # stdout contains counts only, never key material. Reject unexpected output.
    row = json.loads(result.stdout.strip())
    if (set(row) != {'organizations','users','groups','integrations','posts','media','domain_fingerprint'}
            or not re.fullmatch('[0-9a-f]{64}',row['domain_fingerprint'])
            or any(type(v) is not int for k,v in row.items() if k != 'domain_fingerprint')):
        raise SystemExit('Unexpected fixture receipt')
    receipt = json.dumps(row,sort_keys=True)
    if args.receipt: args.receipt.write_text(receipt+'\n')
    print(json.dumps({k:v for k,v in row.items() if k != 'domain_fingerprint'},sort_keys=True))
