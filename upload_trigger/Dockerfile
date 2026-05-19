FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    openjdk-17-jdk \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Nextflow 25.10
ENV NXF_VER=25.10.0

RUN curl -s https://get.nextflow.io | bash && \
    mv nextflow /usr/local/bin/ 

RUN nextflow -version

# Pull sarek pipeline
RUN nextflow pull nf-core/sarek

# Copy config
COPY nextflow.config .

# Default command (optional, Batch overrides anyway)
CMD ["bash"]

# gcloud builds submit --tag us-central1-docker.pkg.dev/omicstack/omicstack/sarek_v1:latest  
