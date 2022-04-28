from client import client
import requests
import time
import collections
import copy
from uhashring import HashRing
import yaml
from kubernetes import client, utils, watch
from datetime import datetime
import logging
from generate_deployment_files import generate_deployment_file

format = "%(asctime)s: %(message)s"
logging.basicConfig(format=format, level=logging.INFO,
                    datefmt="%H:%M:%S")
logger = logging.getLogger("Global Log")

def get_endpoints(api, what, port):
    services = api.list_service_for_all_namespaces(watch=False)
    endpoints = []
    for i in services.items:
        if what in i.metadata.name:
            endpoints.append(i.status.load_balancer.ingress[0].ip + ":" + str(port))
    return endpoints

def create_shard(instance_num):
    k8sClient = client.ApiClient()
    api = client.AppsV1Api(k8sClient)
    generate_deployment_file(1, 3, instance_num, deploy_file_output="")
    deployment_path = f"deployment_files/etcd-deployment-{instance_num}.yaml"
    f = open(deployment_path)
    dep = yaml.load_all(f.read(), Loader=yaml.Loader)
    try:
        response = utils.create_from_yaml(k8sClient, deployment_path)
        
    except ValueError:
        pass
    
    w = watch.Watch()
    core_v1 = client.CoreV1Api()
    start_time = time.time()
    for event in w.stream(func=core_v1.list_namespaced_pod,
                            namespace="default",
                            label_selector=f"etcd-client-{instance_num}",
                            timeout_seconds=60):
        if event["object"].status.phase == "Running":
            w.stop()
            end_time = time.time()
            logger.info("%s started in %0.2f sec", f"etcd-client-{instance_num}", end_time-start_time)
            return

def delete_shard(instance_num):
    k8sClient = client.ApiClient()
    api = client.AppsV1Api(k8sClient)
    res_1 = v1.delete_namespaced_pod(namespace="default", name=f"etcd-{instance_num}-0")
    res_2 = v1.delete_namespaced_pod(namespace="default", name=f"etcd-{instance_num}-1")
    res_3 = v1.delete_namespaced_pod(namespace="default", name=f"etcd-{instance_num}-2")
    res_4 = api.delete_namespaced_stateful_set(name=f"etcd-{instance_num}", namespace="default")
    res_5 = v1.delete_namespaced_persistent_volume_claim(f"data-{instance_num}-etcd-{instance_num}-0", namespace="default")
    res_6 = v1.delete_namespaced_persistent_volume_claim(f"data-{instance_num}-etcd-{instance_num}-1", namespace="default")
    res_7 = v1.delete_namespaced_persistent_volume_claim(f"data-{instance_num}-etcd-{instance_num}-2", namespace="default")
    res_8 = v1.delete_namespaced_service(name=f"etcd-{instance_num}", namespace="default")
    res_9 = v1.delete_namespaced_service(name=f"etcd-client-{instance_num}", namespace="default")
    
    w = watch.Watch()
    core_v1 = client.CoreV1Api()
    start_time = time.time()
    for event in w.stream(func=core_v1.list_namespaced_pod,
                            namespace="default",
                            label_selector=f"etcd-client-{instance_num}",
                            timeout_seconds=60):
        if event["object"].status.phase != "Running":
            w.stop()
            end_time = time.time()
            logger.info("%s started in %0.2f sec", f"etcd-client-{instance_num}", end_time-start_time)
            return
    
def get_server_for_key(ring, key):
    return ring.get_node(key)

def get_path_helper(hash_ring, path):
    server = get_server_for_key(hash_ring, path)
    logging.info(f"Sending get request for {path} to {server}")
    response = requests.get(f"http://{server}/v2/keys/{path}")
    return {"data": response.json(), "server": server}

def delete_path_helper(hash_ring, path):
    server = get_server_for_key(hash_ring, path)
    logging.info(f"Sending delete request for {path} to {server}")
    response = requests.delete(f"http://{server}/v2/keys/{path}")
    return {"data": response.json(), "server": server}

def set_helper(hash_ring, path, value):
    server = get_server_for_key(hash_ring, path)
    logging.info(f"Sending set request for {path} with value {value} to {server}")
    response = requests.put(f"http://{server}/v2/keys/{path}", data={'value': value})
    return {"data": response.json(), "server": server}

def get_all_keys_by_server(server):
    response = requests.get(f"http://{server}/v2/keys/")
    return response.json()

def add_server_and_reshard(hash_ring, server):
    # Add server to ring
    new_hash_ring = copy.deepcopy(hash_ring)
    new_hash_ring.add_node(server)
    
    # Get sorted list of server nodes and their virtual tokens.
    server_list = new_hash_ring.get_points()
    
    # Find positions of all successor nodes to the server (including virtual tokens)
    successor_list = set()
    for i in range(len(server_list)):
        point, node = server_list[i]
        if node == server and server_list[(i + 1) % len(server_list)][1] != server:
            successor_list.add(server_list[(i + 1) % len(server_list)][1])
    
    # Delete keys that need to be resharded from all successors
    to_be_set = []
    for successor in successor_list:
        response = get_all_keys_by_server(successor)
        # print(response)
        if not "nodes" in response["node"]:
            continue
        items = response["node"]["nodes"]
        print(items)
        for item in items:
            # Remove the '/' in front of the key
            key = item["key"][1:len(item['key'])]
            value = item["value"]
            new_target_server = get_server_for_key(new_hash_ring, key)
            print(new_target_server, server)
            if new_target_server == server:
                r_delete = delete_path_helper(hash_ring, key)
                print(r_delete)
                to_be_set.append((key, value))
    
    print(hash_ring.get_nodes())
    print(new_hash_ring.get_nodes())
    print(to_be_set)
    # Add server back to ring and reshard keys that need to be resharded
    hash_ring = new_hash_ring
    for key, value in to_be_set:
        print(f"Resharding key {key} with value {value}")
        r_set = set_helper(hash_ring, key, value)
        print(r_set)
    return hash_ring

def remove_server_and_reshard(hash_ring, server):
    hash_ring.remove_node(server)
    items = get_all_keys_by_server(server)
    if not "nodes" in items["node"]:
        return hash_ring
    items_nodes = items["node"]["nodes"]
    print(items)
    for item in items_nodes:
        key = item["key"]
        value = item["value"]
        r_set = set_helper(hash_ring, key, value)
        print(r_set)
    return hash_ring
        
v1 = client.CoreV1Api()
        
services = v1.list_service_for_all_namespaces(watch=False)

'''
while True:
    - Check if SLO's are being met
        - Ping pods and determine mean response time
        - Ensure that pods are not reaching maximum capacity
    - If SLO's are not being met:
        - Provision an additional etcd StatefulSet cluster
        - Notify sharding ReplicaSet after successful provisioning (use some sort of passcode)
        - Sharders reshard
    - If SLO's are being exceedingly met:
        - Remove an etcd StatefulSet cluster
        - Notify sharding ReplicaSet after successful deletion (use some sort of passcode)
        - Sharders reshard
        

'''

MIN_REQUESTS = 100
MAX_ACCEPTED_RES_TIME = 0.100
MIN_ACCEPTED_RES_TIME= 0.080

time.sleep(1)

rolling_res_time = collections.deque()
rolling_res_time_sum = 0

while True:
    
    # sharder_lb_ip = get_endpoints(v1, "sharder-endpoint", 5000)[0]
    
    etcd_server_ips = get_endpoints(v1, "client", 2379)
    # print(etcd_server_ips)
    hash_ring = HashRing(nodes=etcd_server_ips, hash_fn='ketama')
    # Ping for response time
    for ip in etcd_server_ips:
        ping_res = requests.get(f"http://{ip}/version")
        if ping_res.status_code != 200:
            continue
        res_time = ping_res.elapsed.total_seconds()
        rolling_res_time.append(res_time)
        if len(rolling_res_time) > MIN_REQUESTS:
            dur = rolling_res_time.popleft()
            rolling_res_time_sum -= dur
        rolling_res_time_sum += res_time
    
    
    # ping_res = requests.get(f"http://{sharder_lb_ip}/")
    # if ping_res.status_code != 200:
    #     continue
    # res_time = ping_res.elapsed.total_seconds()
    # print("SHARDER", res_time, datetime.now())
    
    
    avg_res_time = rolling_res_time_sum / len(rolling_res_time)
    print("ETCD" ,avg_res_time, rolling_res_time_sum, len(rolling_res_time), datetime.now())
    if len(rolling_res_time) == MIN_REQUESTS:
        if avg_res_time > MAX_ACCEPTED_RES_TIME:
            print(f"EXCEEDED MAX AVG RESPONSE TIME {avg_res_time}")
            create_shard(len(etcd_server_ips) + 1)
            new_server = get_endpoints(v1, "client", 2379)[-1]
            print(f"Shard {len(etcd_server_ips) + 1} {new_server} created")
            hash_ring = add_server_and_reshard(hash_ring, new_server)
            print(f"Successful resharded")
            rolling_res_time.clear()
            rolling_res_time_sum = 0
            time.sleep(60)
        elif avg_res_time < MIN_ACCEPTED_RES_TIME:
            print(f"UNDER MIN AVG RESPONSE TIME {avg_res_time}")
            hash_ring = remove_server_and_reshard(hash_ring, etcd_server_ips[-1]) 
            print(f"Server {etcd_server_ips[-1]} removed from hash_ring")
            delete_shard(len(etcd_server_ips))
            print(f"Shard {len(etcd_server_ips)} removed from cluster")
            rolling_res_time.clear()
            rolling_res_time_sum = 0
            time.sleep(60)
    time.sleep(1)
    
    
            
        
        
            
   