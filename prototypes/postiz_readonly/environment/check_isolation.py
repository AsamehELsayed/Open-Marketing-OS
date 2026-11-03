"""Read-only checks of actual dev050 containers; no firewall/resource changes."""
import argparse
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent

def command(*args):
    return subprocess.check_output(['docker',*args],cwd=HERE,text=True,timeout=30)

def check():
    network = json.loads(command('network','inspect','dev050_isolated'))[0]
    assert network['Internal'] is True and not network.get('EnableIPv6')
    assert network['Labels'].get('dev050.owner') == 'DEV-050'
    ids = command('compose','-p','dev050','-f','compose.json','ps','-q').split()
    assert len(ids) == 7
    containers = json.loads(command('inspect',*ids))
    for item in containers:
        assert item['State']['Running']
        assert item['Config']['Labels'].get('dev050.owner') == 'DEV-050'
        service = item['Config']['Labels'].get('com.docker.compose.service')
        expected_networks = {'dev050_isolated','dev050_ingress'} if service == 'ingress' else {'dev050_isolated'}
        assert set(item['NetworkSettings']['Networks']) == expected_networks
        if service == 'ingress':
            assert item['HostConfig']['Sysctls']['net.ipv4.ip_forward'] == '0'
        assert item['HostConfig']['Dns'] == ['127.0.0.1']
        assert item['HostConfig']['Sysctls']['net.ipv6.conf.all.disable_ipv6'] == '1'
        for bindings in item['HostConfig'].get('PortBindings',{}).values():
            assert bindings == [{'HostIp':'127.0.0.1','HostPort':'5199'}]
        values = dict(value.split('=',1) for value in item['Config']['Env'] if '=' in value)
        for name,value in values.items():
            if name.upper() in {'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','MCP_SELF_HOSTED_RELAY'}:
                assert not value
        if item['Config']['Labels'].get('com.docker.compose.service') == 'postiz':
            for name in ['OPENAI_API_KEY','X_API_KEY','LINKEDIN_CLIENT_ID','LINKEDIN_CLIENT_SECRET',
                         'FACEBOOK_APP_ID','FACEBOOK_APP_SECRET','NEXT_PUBLIC_SENTRY_DSN','STRIPE_SECRET_KEY']:
                assert not values.get(name)
    # Actual namespace routing plus an inert documentation-IP connectivity probe.
    source = r"""
const fs=require('fs'),net=require('net');
const rows=fs.readFileSync('/proc/net/route','utf8').trim().split('\n').slice(1);
if(rows.some(row=>row.trim().split(/\s+/)[1]==='00000000')) process.exit(2);
const socket=net.connect({host:'192.0.2.1',port:443});
socket.setTimeout(1500);
socket.on('connect',()=>{socket.destroy();process.exit(3)});
socket.on('error',err=>{console.log(JSON.stringify({no_default_route:true,probe_connected:false,error:err.code}));});
socket.on('timeout',()=>{socket.destroy();console.log(JSON.stringify({no_default_route:true,probe_connected:false,error:'TIMEOUT'}));});
"""
    probe = json.loads(command('compose','-p','dev050','-f','compose.json','exec','-T','postiz','node','-e',source))
    assert probe['no_default_route'] and not probe['probe_connected']
    return {'actual_container_count':len(containers),'internal_network':True,'loopback_only':True,
            'external_dns_configured':False,'provider_secrets_configured':False,'namespace_probe':probe,
            'limits':'Routing/containment proof, not a packet capture or proof of every attempted syscall.'}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--receipt',type=Path,required=True)
    args = parser.parse_args()
    result = check()
    args.receipt.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
