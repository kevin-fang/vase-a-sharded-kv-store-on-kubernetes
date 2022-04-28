from locust import FastHttpUser, task
import random
import hashlib

print("key,value,server_ip")


class TestUser(FastHttpUser):
    @task
    def diff_server(self):
        new_key = random.randint(0, 100)
        key_txt = f"{new_key}_value_key"
        value_txt = f"value_{new_key}" + str(random.randint(1, 1000))

        response = self.client.put(
            "/set", name=f"set_key_{new_key}", json={"path": key_txt, "value": value_txt})
        # print(key_txt + "," + value_txt + "," + str(response.json()['server']))
        self.client.get(f"/get/{key_txt}")
