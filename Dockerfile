FROM alpine:3.22 AS build

LABEL Maintainer="g0dzuki99 <chris@chaoscontrol.org>" \
      Description="Master server for Quake III Arena."

RUN apk add --no-cache gcc make musl-dev
COPY . /dpmaster
RUN make -C /dpmaster/src release

FROM alpine:3.22
RUN adduser -S -D -H dpmaster && mkdir -p /var/lib/dpmaster \
    && chown dpmaster:dpmaster /var/lib/dpmaster
COPY --from=build /dpmaster/src/dpmaster /usr/local/bin/dpmaster

EXPOSE 27950/udp

USER dpmaster
VOLUME [ "/var/lib/dpmaster" ]

ENTRYPOINT [ "/usr/local/bin/dpmaster" ]
CMD [ "--flood-protection", "--state-file", "/var/lib/dpmaster/servers.state", "--verbose", "2" ]
