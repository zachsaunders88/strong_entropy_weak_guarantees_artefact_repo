import yaml
from pydantic import BaseModel, Field
from typing import List, Optional

class HostConfig(BaseModel):
    count: int = Field(..., description="Number of hosts to simulate")
    subnet: str = Field(..., description="Subnet for real IPs")

class VIPConfig(BaseModel):
    pool_cidr: str = Field(..., description="CIDR block for virtual IPs")
    mutation_interval: int = Field(..., description="Mutation interval in seconds")
    entropy_source: str = Field("std_random", description="Entropy source for vIP generation: 'std_random' or 'secrets'")
    entropy_source: str = Field("std_random", description="Entropy source for vIP generation: 'std_random' or 'secrets'")
    dead_server_url: str = Field("http://127.0.0.1:8000", description="URL for the DEAD entropy service")
    coordination_scope: Optional[str] = Field(None, description="Scope ID for coordinated permutations (fixes S2)")

class ControllerConfig(BaseModel):
    port: int = 8080
    hosts: HostConfig
    vip: VIPConfig

class GatewayConfig(BaseModel):
    listen_port: int = 8081
    controller_url: str = "http://localhost:8080"

class AppConfig(BaseModel):
    controller: ControllerConfig
    gateway: GatewayConfig
    log_level: str = "INFO"

def load_config(path: str) -> AppConfig:
    with open(path, 'r') as f:
        data = yaml.safe_load(f)
    return AppConfig(**data)
