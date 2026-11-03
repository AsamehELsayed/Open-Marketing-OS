"""Generate scoped overrides from pinned upstream Compose; no Docker mutations."""
import json
import secrets
from pathlib import Path
import yaml

HERE = Path(__file__).resolve().parent
IMAGES = {
    'postiz':'ghcr.io/gitroomhq/postiz-app@sha256:3e2facf0c1ffdfe07b31cebdc5b850767721cb40a7a389dd07485f8e381bb49f',
    'postiz-postgres':'postgres@sha256:e4cb93687b062cd25a0e8f672d85a7708235009d70d31f8a7698d9370092a9c4',
    'postiz-redis':'redis@sha256:00e75fbf4349fea45c7907353554a340c8606ce9f6372afe26ad1039641237a8',
    'temporal-postgresql':'postgres@sha256:b95b87e93ceb7594715d5c71fb6e09f6fc878851ab537735d617325eb9ba8b1b',
    'temporal-elasticsearch':'elasticsearch@sha256:07e9262b856006959e384de7ca9587bc5c6da024ca7042216a9ea26efe4c69c1',
    'temporal':'temporalio/auto-setup@sha256:e4c296d2b16d7607938e71182b9cc0936c90002ff63d97227f195625cf798ea3',
}

def prepare():
    original = yaml.safe_load((HERE/'upstream-compose.yaml').read_text())
    services = {name:original['services'][name] for name in IMAGES}
    for name, service in services.items():
        service['image'] = IMAGES[name]
        service['platform'] = 'linux/amd64'
        service['restart'] = 'no'
        service['networks'] = ['isolated']
        service['dns'] = ['127.0.0.1']
        service['sysctls'] = {'net.ipv6.conf.all.disable_ipv6':'1'}
        service['labels'] = {'dev050.owner':'DEV-050', 'dev050.disposable':'true'}
        service.pop('container_name',None)
        service.pop('ports',None)
    postiz = services['postiz']
    env = postiz['environment']
    env.update(MAIN_URL='http://127.0.0.1:5199', FRONTEND_URL='http://127.0.0.1:5199',
        NEXT_PUBLIC_BACKEND_URL='http://127.0.0.1:5199/api', JWT_SECRET='${DEV050_JWT:?}',
        DATABASE_URL='postgresql://postiz-user:${DEV050_DB:?}@postiz-postgres:5432/postiz-db-local',
        DISABLE_REGISTRATION='true', MASTODON_URL='', NEXT_TELEMETRY_DISABLED='1', DO_NOT_TRACK='1',
        npm_config_offline='true')
    services['postiz-postgres']['environment']['POSTGRES_PASSWORD'] = '${DEV050_DB:?}'
    services['temporal-postgresql']['environment']['POSTGRES_PASSWORD'] = '${DEV050_TEMPORAL_DB:?}'
    services['temporal']['environment'] = [value if not value.startswith('POSTGRES_PWD=') else 'POSTGRES_PWD=${DEV050_TEMPORAL_DB:?}' for value in services['temporal']['environment']]
    services['temporal']['volumes'] = ['./dynamicconfig:/etc/temporal/config/dynamicconfig:ro']
    services['temporal-postgresql']['volumes'] = ['temporal-db:/var/lib/postgresql/data']
    services['temporal-elasticsearch']['volumes'] = ['temporal-es:/usr/share/elasticsearch/data']
    services['temporal-elasticsearch']['environment'].append('ingest.geoip.downloader.enabled=false')
    volumes = {name:{'labels':{'dev050.owner':'DEV-050'}} for name in [
        'postiz-config','postiz-uploads','postgres-volume','postiz-redis-data','temporal-db','temporal-es']}
    # Docker drops published ports on an internal-only network. Reuse the pinned
    # image's existing nginx, without starting its Node application or credentials.
    services['ingress'] = {'image':IMAGES['postiz'],'platform':'linux/amd64','restart':'no',
        'entrypoint':['nginx'],'command':['-g','daemon off;'],
        'volumes':['./ingress.conf:/etc/nginx/nginx.conf:ro'],
        'ports':['127.0.0.1:5199:5199'],'networks':['isolated','ingress'],
        'dns':['127.0.0.1'],'sysctls':{'net.ipv6.conf.all.disable_ipv6':'1','net.ipv4.ip_forward':'0'},
        'labels':{'dev050.owner':'DEV-050','dev050.disposable':'true'},'depends_on':['postiz']}
    config = {'name':'dev050','services':services,'volumes':volumes,
        'networks':{'isolated':{'internal':True, 'enable_ipv6':False, 'labels':{'dev050.owner':'DEV-050'}},
                    'ingress':{'enable_ipv6':False,'labels':{'dev050.owner':'DEV-050'}}}}
    (HERE/'compose.json').write_text(json.dumps(config,indent=2)+'\n')
    (HERE/'images.lock.json').write_text(json.dumps(IMAGES,indent=2)+'\n')
    secret_file = HERE/'.env'
    if not secret_file.exists():
        secret_file.write_text(''.join(f'{name}={secrets.token_hex(32)}\n' for name in ['DEV050_JWT','DEV050_DB','DEV050_TEMPORAL_DB','DEV050_KEY_A','DEV050_KEY_B']))
    print('Prepared dev050-only Compose and local synthetic secrets; no containers started.')

if __name__ == '__main__': prepare()
