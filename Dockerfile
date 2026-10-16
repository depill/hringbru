FROM debian:trixie-slim AS build
ARG ASTERISK_VERSION=22.11.0
ARG ASTERISK_SHA256=3bd5ee040509a3d3cd9b1ba9520c18e6ec0a7e7981ca68c457dcd36ba3c54d94
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates curl pkg-config libedit-dev libjansson-dev \
    libxslt1-dev libsqlite3-dev libssl-dev libsrtp2-dev uuid-dev libxml2-dev libncurses-dev \
    python3 bzip2 patch && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl -fsSLo asterisk.tar.gz "https://downloads.asterisk.org/pub/telephony/asterisk/asterisk-${ASTERISK_VERSION}.tar.gz" \
    && echo "${ASTERISK_SHA256}  asterisk.tar.gz" | sha256sum -c - \
    && tar xzf asterisk.tar.gz --strip-components=1
RUN ./configure --with-pjproject-bundled --with-crypto --with-ssl --with-srtp \
      --without-dahdi --without-pri --without-radius --without-popt \
    && make menuselect.makeopts \
    && menuselect/menuselect --disable BUILD_NATIVE --disable-category MENUSELECT_CORE_SOUNDS \
       --disable-category MENUSELECT_MOH --disable-category MENUSELECT_EXTRA_SOUNDS menuselect.makeopts \
    && make -j2 && make install DESTDIR=/out && rm -rf /out/var/run

FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libedit2 libjansson4 libsqlite3-0 libssl3t64 libsrtp2-1 \
    libuuid1 libxml2 libxslt1.1 libncurses6 openssl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 asterisk && useradd --uid 10001 --gid 10001 --no-create-home asterisk
COPY --from=build /out/ /
COPY scripts/entrypoint.sh /usr/local/bin/hringbru-entrypoint
RUN mkdir -p /usr/share/asterisk && cp -a /var/lib/asterisk/. /usr/share/asterisk/ \
    && mkdir -p /var/run/asterisk /var/lib/asterisk /var/spool/asterisk /var/log/asterisk \
    && chown -R asterisk:asterisk /var/run/asterisk /var/lib/asterisk /var/spool/asterisk /var/log/asterisk
USER 10001:10001
ENTRYPOINT ["/usr/local/bin/hringbru-entrypoint"]
