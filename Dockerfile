FROM caddy:2-alpine
COPY Caddyfile /etc/caddy/Caddyfile
COPY docs /srv/docs
EXPOSE 80
