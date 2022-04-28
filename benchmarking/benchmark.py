#!/usr/bin/env python
from google.oauth2 import service_account
from google.cloud.container_v1 import ClusterManagerClient
from kubernetes import client, config, utils
import google.auth.transport.requests
import yaml
import time
import os
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

KEY_NAME = "compsci-512-svc-account.json"
PROJECT_ID = "etcd-clusters"
ZONE = "us-central1-c"
CLUSTER_ID = "etcd-scaling-testing-cluster"

TESTS = {
    "4a": {
        "sharders": 30,
        "shards": 1
    },
    "4b": {
        "sharders": 30,
        "shards": 3
    },
    "4c": {
        "sharders": 30,
        "shards": 5
    },
    "4d": {
        "sharders": 30,
        "shards": 7
    }
}


SCOPES = ['https://www.googleapis.com/auth/cloud-platform']
credentials = service_account.Credentials.from_service_account_file(
    "../keys/" + KEY_NAME, scopes=SCOPES)
request = google.auth.transport.requests.Request()
credentials.refresh(request)
cluster_manager_client = ClusterManagerClient(credentials=credentials)
cluster = cluster_manager_client.get_cluster(
    project_id=PROJECT_ID, zone=ZONE, cluster_id=CLUSTER_ID)
configuration = client.Configuration()
configuration.host = "https://"+cluster.endpoint+":443"
configuration.verify_ssl = False
configuration.api_key = {"authorization": "Bearer " + credentials.token}
client.Configuration.set_default(configuration)
v1 = client.CoreV1Api()
k8sClient = client.ApiClient()
api = client.AppsV1Api(k8sClient)


def delete_shard(instance_num, num_replicas_per_set):
    for i in range(num_replicas_per_set + 1):
        res_i = v1.delete_namespaced_pod(
            namespace="default", name=f"etcd-{instance_num}-{i}")
        print(f"Deleted etcd-{instance_num}-{i}")
    res_4 = api.delete_namespaced_stateful_set(
        name=f"etcd-{instance_num}", namespace="default")
    res_5 = v1.delete_namespaced_persistent_volume_claim(
        f"data-{instance_num}-etcd-{instance_num}-0", namespace="default")
    res_6 = v1.delete_namespaced_persistent_volume_claim(
        f"data-{instance_num}-etcd-{instance_num}-1", namespace="default")
    res_7 = v1.delete_namespaced_persistent_volume_claim(
        f"data-{instance_num}-etcd-{instance_num}-2", namespace="default")
    res_8 = v1.delete_namespaced_service(
        name=f"etcd-{instance_num}", namespace="default")
    res_9 = v1.delete_namespaced_service(
        name=f"etcd-client-{instance_num}", namespace="default")
    print(f"Successfully deleted shard {instance_num}")


def delete_sharder():
    pods = v1.list_pod_for_all_namespaces(watch=False)
    sharder_name = ""
    for i in pods.items:
        if "sharder" in i.metadata.name:
            sharder_name = i.metadata.name
    print(f"Sharder name: {sharder_name}")
    try:
        res_1 = v1.delete_namespaced_pod(
            namespace="default", name=sharder_name)
        res_2 = api.delete_namespaced_deployment(
            name=f"etcd-sharder", namespace="default")
        res_3 = v1.delete_namespaced_service(
            name=f"sharder-endpoint", namespace="default")
        print("Successfully deleted sharder")
        return True
    except client.ApiException:
        print(f"Skipping deletion of sharder")
        return False


def generate_deployments(num_shards, num_replicas):
    # always 3 replicas per statefulset ("highly available")
    # instance num always 1
    os.system(
        f"cd .. && python generate_deployment_files.py {num_shards} 3 1 {num_replicas}")


def deploy(filename):
    k8sClient = client.ApiClient()
    api = client.AppsV1Api(k8sClient)
    deployment_path = "../deployment_files/" + filename
    f = open(deployment_path)
    try:
        response = utils.create_from_yaml(k8sClient, deployment_path)
        print(f"Deployed {deployment_path}")
    except ValueError as e:
        pass


def deploy_etcd_shard(instance_num):
    deploy(f"etcd-deployment-{instance_num}.yaml")


for test_name, test_params in TESTS.items():
    print(f"Running benchmark {test_name}, with parameters {test_params}")
    # print("Listing pods with their IPs:")
    pods = v1.list_pod_for_all_namespaces(watch=False)
    instances = set()
    num_replicas_per_set = 1
    for i in pods.items:
        if i.metadata.namespace == "default":
            # print("%s\t%s\t%s" %
            #       (i.status.pod_ip, i.metadata.namespace, i.metadata.name))
            inst_num = i.metadata.name.split("-")[1]
            replica_num = i.metadata.name.split("-")[2]
            if inst_num.isnumeric():
                instances.add(int(inst_num))
                num_replicas_per_set = max(
                    num_replicas_per_set, int(replica_num))
    # print(instances)
    # print(num_replicas_per_set)

    # Take down existing shards
    for i in list(instances):
        try:
            delete_shard(i, num_replicas_per_set)
        except client.ApiException:
            print(f"Skipping deletion of shard {i}")
    sharder_deleted = delete_sharder()

    # Generate x sharding deployments
    NUM_SHARDS = test_params["shards"]
    NUM_REPLICAS = test_params["sharders"]
    generate_deployments(NUM_SHARDS, NUM_REPLICAS)

    # Deploy sharding deployments
    if sharder_deleted:
        print("Sleeping for 60s to allow for sharder deletion")
        time.sleep(60)
    deploy("sharder-deployment.yaml")
    for i in range(1, NUM_SHARDS + 1):
        deploy_etcd_shard(i)
        print(f"Deployed etcd shard {i}")

    # Find sharder endpoint ip
    svcs = v1.list_service_for_all_namespaces()
    sharder_ip = ""
    found = False

    while not found:
        svcs = v1.list_service_for_all_namespaces()
        try:
            for item in svcs.items:
                if item.metadata.name == "sharder-endpoint":
                    sharder_ip = item.status.load_balancer.ingress[0].ip
                    print(sharder_ip)
                    found = True
        except TypeError:
            time.sleep(5)
            print("Load balancer not found. Waiting...")

    print("Load balancer found. Sleeping for 60 seconds to allow for shards to start...")
    time.sleep(60)

    print("Running benchmark. This will take 5 minutes...")
    # Run benchmark
    BENCHMARK_NAME = test_name
    if not os.path.isdir(BENCHMARK_NAME):
        os.mkdir(BENCHMARK_NAME)
    os.system(
        f"locust --host http://{sharder_ip}:5000  --headless --csv={BENCHMARK_NAME}/results -u 500 -r 50 --run-time 5m --html={BENCHMARK_NAME}/results_{BENCHMARK_NAME}.html")
