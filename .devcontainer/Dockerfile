# Simple dev environment with Python + Node + Docker CLI
FROM mcr.microsoft.com/devcontainers/base:ubuntu-22.04
#FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    nodejs npm \
    docker.io git curl ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Ensure sudo and passwd are installed, then create vscode user
RUN apt-get update && apt-get install -y sudo passwd \
  && useradd -ms /bin/bash vscode || true \
  && usermod -aG sudo vscode \
  && echo 'vscode ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers

USER vscode
WORKDIR /workspace
