# Зеркало для белых списков: Serverless Container

Проверено 2026-10-07 на iPhone (4G, белые списки):

| адрес | открывается |
|---|---|
| `ya.ru`, `storage.yandexcloud.net`, `functions.yandexcloud.net` | да |
| `<id>.containers.yandexcloud.net` (84.201.162.181) | да |
| `<id>.apigw.yandexcloud.net` старого вида (84.201.185.129) | да |
| `<id>.<…>.apigw.yandexcloud.net` — так называются новые шлюзы (158.160.x) | **нет** |
| ВМ yavds 89.169.161.53, `ns.ikrya.ru`, `web.telegram.org` | **нет** |

Фильтр — по IP: адреса ВМ и новых API-шлюзов Yandex Cloud в список не входят,
а общие фронтенды Яндекса входят. Поэтому зеркало — бессерверный контейнер:
nginx внутри проксирует всё на `ns.ikrya.ru`. Telegram в этом режиме тоже не
открывается, так что зеркало — для браузера и приложения на домашнем экране.

## Создание

Нужны `yc` (`curl -sSL https://storage.yandexcloud.net/yandexcloud-yc/install.sh | bash`,
затем `yc init`) и запущенный Docker.

```bash
yc container registry create --name netschool
REGISTRY=$(yc container registry get netschool --format json | jq -r .id)
yc container registry configure-docker
docker build --platform linux/amd64 -t cr.yandex/$REGISTRY/netschool-mirror:1 deploy/yandex-container
docker push cr.yandex/$REGISTRY/netschool-mirror:1

yc iam service-account create --name netschool-mirror
SA=$(yc iam service-account get netschool-mirror --format json | jq -r .id)
FOLDER=$(yc config get folder-id)
yc resource-manager folder add-access-binding $FOLDER --role container-registry.images.puller --subject serviceAccount:$SA

yc serverless container create --name netschool-mirror
yc serverless container revision deploy --container-name netschool-mirror \
  --image cr.yandex/$REGISTRY/netschool-mirror:1 --service-account-id $SA \
  --cores 1 --core-fraction 5 --memory 128MB --concurrency 16 --execution-timeout 80s
yc serverless container allow-unauthenticated-invoke netschool-mirror
yc serverless container get netschool-mirror --format json | jq -r .url
```

Полученный адрес `https://<id>.containers.yandexcloud.net/` прописать на сервере
бота в `.env` как `NETSCHOOL_MIRROR_URL` — в `/app_install` и `/app_reset`
появится кнопка «Резервный адрес».
