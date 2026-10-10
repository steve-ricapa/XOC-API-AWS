# IC-1 LOCAL, baseline built from the XOC-MCP clone, never LAB source mounts.
FROM xoc-mcp-ic1:baseline
USER 0:0
COPY extra/ /ic1/extra/
COPY requirements-linux.lock.txt /ic1/requirements-linux.lock.txt
RUN ["/opt/xoc-l2/bin/python", "-m", "pip", "--isolated", "install", "--no-index", "--no-deps", "--require-hashes", "--find-links", "/ic1/extra", "--target", "/ic1/dependencies", "-r", "/ic1/requirements-linux.lock.txt"]
COPY backend/ /ic1/backend/
COPY harness/ /ic1/harness/
COPY source-manifest.json /ic1/source-manifest.json
USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["/opt/xoc-l2/bin/python", "-I", "-B", "/ic1/harness/run.py"]
