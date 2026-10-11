FROM alpine:latest
RUN apk add --no-cache squashfs-tools
ENTRYPOINT ["unsquashfs"]
