# syntax=docker/dockerfile:1
# adservice v0.10.1, pinned by digest
FROM gcr.io/google-samples/microservices-demo/adservice@sha256:4780e3e9dacc787ebf1e338f2cb989823f92b2cf7ab9935d554bd9d0a687e444

# Upstream v0.10.1 does not emit adservice traces. Instrument its gRPC server
# without changing application behavior; pin and verify the official agent.
ADD --checksum=sha256:f787eb6c7f3d18e69a431e108a15278d25ee37f83d68b678f621e063f3988f82 --chmod=0444 https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v2.32.0/opentelemetry-javaagent.jar /opt/otel/opentelemetry-javaagent.jar
ENV JAVA_TOOL_OPTIONS="-javaagent:/opt/otel/opentelemetry-javaagent.jar"
