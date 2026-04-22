#!/bin/bash
# Start Mininet topology
# Must be run as root

if [ "$EUID" -ne 0 ]; then 
  echo "Please run as root"
  exit
fi

# Ensure PYTHONPATH includes the project root
export PYTHONPATH=$(pwd)

python3 mininet/topo.py
