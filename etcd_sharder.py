from flask import Flask, request, jsonify, make_response
import logging
import threading
import requests
import copy
from client import client
from uhashring import HashRing

app = Flask(__name__)
lock = threading.Lock()


format = "%(asctime)s: %(message)s"
logging.basicConfig(format=format, level=logging.INFO,
                    datefmt="%H:%M:%S")
logger = logging.getLogger("Global Log")


def get_endpoints(api, what, port):
    services = api.list_service_for_all_namespaces(watch=False)
    endpoints = []
    for i in services.items:
        if what in i.metadata.name:
            endpoints.append(
                i.status.load_balancer.ingress[0].ip + ":" + str(port))
    return endpoints


v1 = client.CoreV1Api()

avail_servers = get_endpoints(v1, "client", 2379)

print(avail_servers)

hash_ring = HashRing(nodes=avail_servers, hash_fn='ketama')

# request_queue = collections.deque()

request_lock = False


def toBinary(a):
    m = []
    for i in a:
        m.append(int(bin(ord(i))[2:]))
    return m


def get_server_for_key(ring, key):
    return ring.get_node(key)


def add_server_and_reshard(server):
    # Add server to ring
    global hash_ring
    new_hash_ring = copy.deepcopy(hash_ring)
    print(id(new_hash_ring), id(hash_ring))
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
        items = response["node"]["nodes"]
        # print(items)
        for item in items:
            # Remove the '/' in front of the key
            key = item["key"][1:len(item['key'])]
            value = item["value"]
            new_target_server = get_server_for_key(new_hash_ring, key)
            print(new_target_server, server)
            if new_target_server == server:
                r_delete = delete_path_helper(key)
                print(r_delete)
                to_be_set.append((key, value))

    # Add server back to ring and reshard keys that need to be resharded
    hash_ring = new_hash_ring
    for key, value in to_be_set:
        print(f"Resharding key {key} with value {value}")
        r_set = set_helper(key, value)
        print(r_set)


def remove_server_and_reshard(server, items):
    hash_ring.remove_node(server)
    for item in items:
        key = item["key"]
        value = item["value"]
        r_set = set_helper(key, value)
        print(r_set)


def get_all_keys_by_server(server):
    response = requests.get(f"http://{server}/v2/keys/")
    return response.json()

# def delete_all_keys_by_server(server):
#     response = requests.delete(f"http://{server}/v2/keys/nodes?dir=true")
#     return response.json()


@app.route("/")
def index():
    return jsonify(avail_servers)


# @app.route("/reset", methods=["GET"])
# def reset():
#     for server in avail_servers:
#         requests.get(f"{server}/reset")

# Get entire store: http://localhost:8080/get


'''
Test resharding 
'''


@app.route("/add_server_and_reshard/<server>", methods=["GET"])
def test_add_server_and_reshard(server):

    response = requests.get(f"http://{server}/version").json()
    print(response)
    if not "etcdserver" in response:
        return server, 401

    add_server_and_reshard(server)

    return server, 200


'''
Get a value by key
'''


@app.route("/get/<path>", methods=["GET"])
def get_path(path):
    global hash_ring
    hash_ring = HashRing(nodes=get_endpoints(
        v1, "client", 2379), hash_fn='ketama')
    return get_path_helper(path)


def get_path_helper(path):
    server = get_server_for_key(hash_ring, path)
    logging.info(f"Sending get request for {path} to {server}")
    response = requests.get(f"http://{server}/v2/keys/{path}")
    return {"data": response.json(), "server": server}


'''
Delete a key and its value 
'''


@app.route("/delete/<path>", methods=["DELETE"])
def delete_path(path):
    global hash_ring
    hash_ring = HashRing(nodes=get_endpoints(
        v1, "client", 2379), hash_fn='ketama')
    return delete_path_helper(path)


def delete_path_helper(path):
    server = get_server_for_key(hash_ring, path)
    logging.info(f"Sending delete request for {path} to {server}")
    response = requests.delete(f"http://{server}/v2/keys/{path}")
    return {"data": response.json(), "server": server}


'''
Set a key and value
'''


@app.route("/set", methods=["PUT"])
def set_name():
    global hash_ring
    hash_ring = HashRing(nodes=get_endpoints(
        v1, "client", 2379), hash_fn='ketama')
    data = request.get_json()
    return set_helper(data['path'], data['value'])


def set_helper(path, value):
    server = get_server_for_key(hash_ring, path)
    logging.info(
        f"Sending set request for {path} with value {value} to {server}")
    response = requests.put(
        f"http://{server}/v2/keys/{path}", data={'value': value})
    return {"data": response.json(), "server": server}


if __name__ == "__main__":
    app.run("0.0.0.0", threaded=True, port=8080, debug=True)
