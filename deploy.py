'''
Deploy the Uniswap automatically on VeChain.
'''
import json
import secrets
import sys
import time
from typing import Union

import requests
from thor_devkit import abi, cry, transaction
from thor_devkit.cry import secp256k1

TARGETS = {
    'vvet': 'vvet/build/contracts/VVET9.json',
    'factory': 'core/build/contracts/UniswapV2Factory.json',
    'router': 'periphery/build/contracts/UniswapV2Router02.json'
}

VTHO_CONTRACT = '0x0000000000000000000000000000456e65726779'


def make_chaintag(hex_str: str):
    return int(hex_str, 16)


def read_json_file(path_like: str) -> dict:
    ''' Read json file '''
    with open(path_like, 'r') as f:
        return json.load(f)


def get_bytecode(contract_meta: dict, key: str = 'bytecode') -> bytes:
    ''' Get bytecode from contract meta data '''
    return bytes.fromhex( contract_meta[key] )


def build_params(types: list, args: list) -> bytes:
    ''' ABI encode params '''
    return abi.Coder.encode_list(types, args)


def _build_url(base: str, tail: str) -> str:
    ''' Build URLs '''
    return base.rstrip('/') + '/' + tail.lstrip('/')


def get_block(network: str, id_or_number: str = 'best') -> dict:
    ''' Get a block, default get "best" block '''
    url = _build_url(network, f'blocks/{id_or_number}')
    r = requests.get(url, headers={'accept':'application/json'})
    if not (r.status_code == 200):
        raise Exception(f'Cant connect to {url}, error {r.text}')
    return r.json()


def best_block(network: str) -> dict:
    ''' Get best block '''
    return get_block(network)


def get_account(network: str, account_id: str, block: str = "best") -> dict:
    ''' Query account status against the best (or your choice) block '''
    url = _build_url(network, f'/accounts/{account_id}?revision={block}')
    r = requests.get(url, headers={'accept':'application/json'})
    if not (r.status_code == 200):
        raise Exception(f'Cant connect to {url}, error {r.text}')
    return r.json()


def _calc_blockRef(block: dict) -> str:
    ''' Calculate blockRef from a block '''
    return block['id'][0:18]


def _calc_nonce() -> str:
    ''' Get a random number for nonce '''
    return int(secrets.token_hex(8), 16)


def _calc_address(priv: bytes) -> str:
    public_key = secp256k1.derive_publicKey(priv)
    _address_bytes = cry.public_key_to_address(public_key)
    address = '0x' + _address_bytes.hex()
    return address


def build_tx(priv: str, network: str, chainTag: int, to: str, value: int, data: str, gas: int, dependsOn=None) -> str:
    ''' Build a tx '''
    block = best_block(network)
    blockRef = _calc_blockRef(block)
    nonce = _calc_nonce()
    body = {
        "chainTag": chainTag,
        "blockRef": blockRef,
        "expiration": 32,
        "clauses": [
            {
                "to": to,
                "value": value,
                "data": data
            }
        ],
        "gasPriceCoef": 0,
        "gas": gas,
        "dependsOn": dependsOn,
        "nonce": nonce
    }

    tx = transaction.Transaction(body)
    priv_key = bytes.fromhex(priv)
    message_hash = tx.get_signing_hash()
    signature = cry.secp256k1.sign(message_hash, priv_key)
    tx.set_signature(signature)

    return '0x' + tx.encode().hex()


def post_tx(network: str, raw: str) -> str:
    ''' Post tx, get tx id '0x...' '''
    url = _build_url(network, 'transactions')
    r = requests.post(
        url,
        headers={
            'accept': 'application/json',
            'Content-Type': 'application/json'
        },
        json={"raw": raw }
    )
    if not (r.status_code == 200):
        raise Exception(f"Creation error? HTTP: {r.status_code} {r.text}")

    return r.json()['id']


def tx_receipt(network: str, tx_id: str) -> Union[dict, None]:
    ''' Fetch tx receipt as a dict, or None '''
    url = _build_url(network, f'transactions/{tx_id}/receipt')
    r = requests.get(url, headers={'accept':'application/json'})
    if not (r.status_code == 200):
        raise Exception(f"Creation error? HTTP: {r.status_code} {r.text}")

    return r.json()


def is_reverted(receipt: dict) -> bool:
    ''' Check receipt to see if tx is reverted '''
    return receipt['reverted']


def _find_created_contracts(receipt: dict) -> list:
    ''' Read receipt and return a list of contract addresses created '''
    a = []
    for x in receipt['outputs']:
        if x.get('contractAddress'):
            a.append(x.get('contractAddress'))
    
    return a


def wait_for_receipt(network: str, tx_id: str, wait_for: int = 20) -> dict:
    ''' Wait for wait_for seconds (default 10s) to find the receipt on-chain '''
    interval = 3
    rounds = wait_for // interval
    receipt = None
    for _ in range(rounds):
        receipt = tx_receipt(network, tx_id)
        if receipt:
            break
        else:
            time.sleep(3)

    if not receipt:
        raise Exception(f"Cannot get receipt after {wait_for}s, tx dropped?")

    return receipt


def deploy(network: str, chainTag: int, contract_meta: dict, types: list, params: list, priv: str, to: str, value: int, gas: int) -> str:
    ''' Deploy a smart contract to the chain '''
    print(f'Deploy contract: <{contract_meta["contractName"]}>')
    if not types:
        data_bytes = get_bytecode(contract_meta)
    else:
        data_bytes = get_bytecode(contract_meta) + build_params(types, params)
    data = '0x' + data_bytes.hex()
    # print(f'data: {data}')
    raw = build_tx(priv, network, chainTag, to, value, data, gas, None)
    tx_id = post_tx(network, raw)
    print(f'tx: {tx_id}')

    receipt = wait_for_receipt(network, tx_id)

    if is_reverted(receipt):
        raise Exception('reverted')
    else:
        addrs = _find_created_contracts(receipt)
        print(f"Deployed on: {addrs[0]}")
        return addrs[0]


def call_function(network:str, chainTag: str, abi_dict: dict, func_params: list, priv: str, to: str, value: int, gas: int) -> str:
    ''' Call a smart contract function on-chain '''
    f1 = abi.FUNCTION(abi_dict)
    print(f'Call contract: {to}')
    print(f'function: {f1["name"]}')
    print(f'params: {func_params}')
    f = abi.Function(f1)
    data = f.encode(func_params, to_hex=True)

    raw = build_tx(priv, network, chainTag, to, value, data, gas, None)
    tx_id = post_tx(network, raw)
    print(f'tx: {tx_id}')

    receipt = wait_for_receipt(network, tx_id)

    if is_reverted(receipt):
        raise Exception('reverted')
    
    return tx_id


def find_func_abi(contract_meta: dict, func_name: str) -> Union[dict, None]:
    ''' Find the function by name in the contract meta '''
    abis = contract_meta["abi"]
    for each in abis:
        if each.get('name') == func_name:
            return each


if __name__ == "__main__":
    # sys.argv = [script_name, private_key, netowrk, chaintag]
    DEPLOYER = {
        'address': _calc_address(bytes.fromhex(sys.argv[1])),
        'private': sys.argv[1]
    }
    NETWORK = sys.argv[2] # eg. 'https://solo.veblocks.net'
    CHAIN_TAG = int(sys.argv[3], 16) # eg. '0xa4'

    print('Deployer Balance:')
    acc = get_account(NETWORK, DEPLOYER['address'])
    print('VET:', int(acc['balance'], 16) / (10 ** 18))
    print('VTHO:', int(acc['energy'], 16) / (10 ** 18))
    print('EOA:', not acc['hasCode'])

    # Need at least 14k vtho to deploy those contracts!
    assert int(acc['energy'], 16) / (10 ** 18) > 14000, "Insufficient VTHO"
    assert (not acc['hasCode']), "Only EOA accouts can deploy this"

    # Deploy VVET (3000 VTHO)
    vvet = read_json_file(TARGETS['vvet'])
    vvet_contract_addr = deploy(NETWORK, CHAIN_TAG, vvet, None, None, DEPLOYER['private'], None, 0, 3000000)
    
    # Deploy Factory (3000 VTHO)
    factory = read_json_file(TARGETS['factory'])
    fee_to_setter = DEPLOYER['address']
    factory_contract_addr = deploy(NETWORK, CHAIN_TAG, factory, ['address'], [fee_to_setter], DEPLOYER['private'], None, 0, 3000000)

    # Deploy Router (5000 VTHO)
    router = read_json_file(TARGETS['router'])
    router_contract_addr = deploy(NETWORK, CHAIN_TAG, router, ['address','address'], [factory_contract_addr, vvet_contract_addr], DEPLOYER['private'], None, 0, 5000000)
    
    # Create VVET/VTHO pair (2500 VTHO)
    createPair_abi = find_func_abi(factory, 'createPair')
    if not createPair_abi:
        raise Exception("Cannot find createPair abi")
    
    call_function(NETWORK, CHAIN_TAG, createPair_abi, [vvet_contract_addr, VTHO_CONTRACT], DEPLOYER['private'], factory_contract_addr, 0, 2500000)
