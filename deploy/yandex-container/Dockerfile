# Зеркало приложения для мобильного интернета по белым спискам — см. README.md.
FROM nginx:1.27-alpine
# Serverless Containers сообщает порт в $PORT; шаблон подставляется при старте.
ENV PORT=8080
COPY default.conf.template /etc/nginx/templates/default.conf.template
