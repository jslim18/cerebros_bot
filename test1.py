import json
import requests
from requests.auth import HTTPBasicAuth

import subprocess

url = "http://public.coindaddy.io:4000/api/"
headers = {'content-type': 'application/json'}
auth = HTTPBasicAuth('rpc', '1234')

addy = '1AXgQdNmANYQgu4KXYRFTCKS6A26no9bek'

def isHoldingESCX(id):
   addy = readBlockstack(id)
   bal = getESCXBalance(addy)
   return (bal >= 10.0)

def getESCXBalance(address): 
   try:
      payload = {
         "method": "get_balances",
         "params": {
            "filters":[{"field": "address", "op": "==", "value": address},
                       {"field": "asset", "op": "==", "value": "ESCX"}],
            "filterop": "and"
            },
          "jsonrpc":"2.0",
          "id":0
         }
      response = requests.post(url, data=json.dumps(payload), headers=headers, auth=auth)
      json_data = response.json()
      #quantity = json_data.quantity 
      return (json_data['result'].pop()['quantity']) / 100000000
   except: 
      return 0;

def readBlockstack(id):
   try:
      p = subprocess.check_output(['blockstack','lookup',id])
      data = json.loads(p.decode('utf-8'))
      accounts = data['profile']['account']
      bitcoins = [item["identifier"] for item in accounts
                  if item['service'] == 'bitcoin']
      return bitcoins[0]
   except:
       return ""


print(readBlockstack('jslim18.id'))
print(isHoldingESCX('jslim18.id'))
