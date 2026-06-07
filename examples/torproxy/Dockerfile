ARG TOR_BASE_IMAGE=debian@sha256:f9c6a2fd2ddbc23e336b6257a5245e31f996953ef06cd13a59fa0a1df2d5c252
FROM ${TOR_BASE_IMAGE}

RUN apt-get update && apt-get install -y --no-install-recommends \
        tor \
        obfs4proxy \
        snowflake-client \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/lib/tor \
    && chown -R debian-tor:debian-tor /var/lib/tor

COPY torrc /etc/tor/torrc
RUN chown debian-tor:debian-tor /etc/tor/torrc

USER debian-tor
EXPOSE 9050

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
  CMD curl -sf --socks5-hostname localhost:9050 https://check.torproject.org/api/ip || exit 1

CMD ["tor", "-f", "/etc/tor/torrc"]
