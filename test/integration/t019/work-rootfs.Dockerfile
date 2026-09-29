FROM scratch
COPY cao-work-supervisor /cao-work-supervisor
ARG CAO_WORK_SUPERVISOR_SOURCE_SHA256
LABEL org.cao.work.supervisor.protocol="1"
LABEL org.cao.work.supervisor.source_sha256="${CAO_WORK_SUPERVISOR_SOURCE_SHA256}"
