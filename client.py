from google.oauth2 import service_account
from google.cloud.container_v1 import ClusterManagerClient
from kubernetes import client, config, utils
import google.auth.transport.requests
import yaml
import os
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

project_id = "etcd-clusters"
zone = "us-central1-c"
cluster_id = "etcd-scaling-testing-cluster"

SCOPES = ['https://www.googleapis.com/auth/cloud-platform']
credentials = service_account.Credentials.from_service_account_file(
    "keys/compsci-512-svc-account.json", scopes=SCOPES)
request = google.auth.transport.requests.Request()
credentials.refresh(request)
cluster_manager_client = ClusterManagerClient(credentials=credentials)
cluster = cluster_manager_client.get_cluster(
    project_id=project_id, zone=zone, cluster_id=cluster_id)
configuration = client.Configuration()
configuration.host = "https://"+cluster.endpoint+":443"
configuration.verify_ssl = False
configuration.api_key = {"authorization": "Bearer " + credentials.token}
client.Configuration.set_default(configuration)


