FROM python:3.10-alpine AS builder
COPY ./requirements.txt /home
RUN pip install --no-cache-dir -r /home/requirements.txt

FROM python:3.10-alpine
EXPOSE 8080
WORKDIR /home
COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY . /home
RUN apk add --no-cache git \
    && rm -rf /tmp/*
ENTRYPOINT ["/home/setup.sh"]