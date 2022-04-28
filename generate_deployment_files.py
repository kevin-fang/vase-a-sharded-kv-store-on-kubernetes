import sys
import os
import stat
# Arg 1: Number of deployments
# Arg 2: Number of replicas per StatefulSet
# Arg 3: Specific instance number start range

def generate_deployment_file(num_deployments, num_replicas, instance_num, deploy_file_output=""):
  for num in range(instance_num, instance_num + num_deployments):
      idf = num
      yaml_output = f'''
---
apiVersion: v1
kind: Service
metadata:
  name: etcd-client-{idf}
spec:
  type: LoadBalancer
  ports:
  - name: etcd-client
    port: 2379
    protocol: TCP
    targetPort: 2379
  selector:
    app: etcd-{idf}
---
apiVersion: v1
kind: Service
metadata:
  name: etcd-{idf}
spec:
  clusterIP: None
  ports:
  - port: 2379
    name: client
  - port: 2380
    name: peer
  selector:
    app: etcd-{idf}
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: etcd-{idf}
  labels:
    app: etcd-{idf}
spec:
  serviceName: etcd-{idf}
  replicas: {num_replicas}
  selector:
    matchLabels:
      app: etcd-{idf}
  template:
    metadata:
      name: etcd-{idf}
      labels:
        app: etcd-{idf}
    spec:
      containers:
      - name: etcd
        image: quay.io/coreos/etcd:latest
        ports:
        - containerPort: 2379
          name: client
        - containerPort: 2380
          name: peer
        volumeMounts:
        - name: data-{idf}
          mountPath: /tmp/var/run/etcd
        command:
          - /bin/sh
          - -c
          - |
            PEERS="etcd-{idf}-0=http://etcd-{idf}-0.etcd-{idf}:2380,etcd-{idf}-1=http://etcd-{idf}-1.etcd-{idf}:2380,etcd-{idf}-2=http://etcd-{idf}-2.etcd-{idf}:2380"
            exec etcd --name ${{HOSTNAME}} \
              --listen-peer-urls http://0.0.0.0:2380 \
              --listen-client-urls http://0.0.0.0:2379 \
              --advertise-client-urls http://${{HOSTNAME}}.etcd:2379 \
              --initial-advertise-peer-urls http://${{HOSTNAME}}:2380 \
              --initial-cluster-token etcd-cluster-1 \
              --initial-cluster ${{PEERS}} \
              --initial-cluster-state new \
              --data-dir /tmp/var/run/etcd/default.etcd
      
  
  volumeClaimTemplates:
  - metadata:
      name: data-{idf}
    spec:
      storageClassName: etcd-pd-ssd-1
      accessModes: [ "ReadWriteOnce" ]
      resources:
        requests:
          storage: 1Gi
    '''
      f = open(f"deployment_files/etcd-deployment-{idf}.yaml", "w")
      f.write(yaml_output)
      f.close()

      deploy_file_output += f"\nkubectl create -f etcd-deployment-{idf}.yaml"
      
  return deploy_file_output


if __name__ == "__main__":
    if len(sys.argv) != 5:
      print("Usage: ./generate_deployment_files.py <number of deployment .yaml files to generate> <replicas per StatefulSet> <instance number to start> <replicas for ReplicaSet>")
      sys.exit(1)

    num_deployments = int(sys.argv[1])
    num_replicas = int(sys.argv[2])
    instance_num = int(sys.argv[3])
    num_replicas_rs = int(sys.argv[4])

    deploy_file_output = '''#!/bin/bash

kubectl create -f storage-class.yaml
kubectl create -f sharder-deployment.yaml
    '''
    
    deploy_file_output = generate_deployment_file(num_deployments, num_replicas, instance_num, deploy_file_output=deploy_file_output)
    with open("deployment_files/deploy.sh", "w") as deploy_file:
      deploy_file.write(deploy_file_output)
    os.chmod("deployment_files/deploy.sh", os.stat('deployment_files/deploy.sh').st_mode | stat.S_IEXEC)
    with open("deployment_files/sharder-deployment.yaml", "w") as deploy_file:
      deploy_file.write(f'''
---
apiVersion: v1
kind: Service
metadata:
  name: sharder-endpoint
spec:
  type: LoadBalancer
  ports:
    - name: etcd-client
      port: 5000
      protocol: TCP
      targetPort: 5000
  selector:
    app: etcd_sharder

---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: etcd-sharder
  labels:
    app: etcd-sharder
spec:
  replicas: {num_replicas_rs}
  selector:
    matchLabels:
      app: etcd_sharder
  template:
    metadata:
      name: etcd_sharder
      labels:
        app: etcd_sharder
    spec:
      containers:
        - name: sharder
          image: kfang1233/etcd-sharder:latest
          ports:
            - containerPort: 5000
              name: client
          resources:
            limits:
              cpu: 300m
            requests:
              cpu: 200m
''')
    