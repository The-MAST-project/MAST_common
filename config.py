import socket

import pymongo
from pymongo.errors import ConnectionFailure, PyMongoError
import logging
from typing import List
from common.utils import init_log, deep_dict_update, deep_dict_difference, deep_dict_is_empty
from copy import deepcopy
from cachetools import TTLCache, cached

logger = logging.getLogger('config')
init_log(logger)
WEIZMANN_DOMAIN: str = 'weizmann.ac.il'

cache = TTLCache(maxsize=100, ttl=30)


# Enable debug logging for PyMongo
# logging.basicConfig(level=logging.DEBUG)
logging.getLogger('pymongo').setLevel(logging.WARNING)


class Config:
    _instance = None
    _initialized: bool = False

    NUMBER_OF_UNITS = 20

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
        return cls._instance

    def __init__(self, site: str = 'wis'):
        if self._initialized:
            return

        try:
            client = pymongo.MongoClient(f"mongodb://mast-{site}-control.weizmann.ac.il:27017/")
            self.db = client['mast']
        except ConnectionFailure as e:
            logger.error(f"{e}")

        self._initialized = True

    @cached(cache)
    def get_unit(self, unit_name: str = None) -> dict:
        """
        Gets a unit's configuration.  By default, this is the ['config']['units']['common']
         entry. If a unit-specific entry exists it overrides the 'common' entry.
        """
        coll = self.db['units']
        common_conf = coll.find_one({'name': 'common'})
        del common_conf['_id']
        ret: dict = deepcopy(common_conf)

        if not unit_name:
            unit_name = socket.gethostname()

        # override with unit-specific config
        unit_conf: dict = coll.find_one({'name': unit_name})
        del unit_conf['_id']
        if unit_conf:
            deep_dict_update(ret, unit_conf)

        # resolve power-switch name and ipaddr
        if unit_name:
            ret['name'] = unit_name
            if ret['power_switch']['network']['host'] == 'auto':
                switch_host_name = unit_name.replace('mast', 'mastps') + '.' + WEIZMANN_DOMAIN
                ret['power_switch']['network']['host'] = switch_host_name
                if 'ipaddr' not in ret['power_switch']['network']:
                    try:
                        ipaddr = socket.gethostbyname(switch_host_name)
                        ret['power_switch']['network']['ipaddr'] = ipaddr
                    except socket.gaierror:
                        logger.warning(f"could not resolve {switch_host_name=}")

        return ret

    def set_unit(self, unit_name: str = None, unit_conf: dict = None):
        if not unit_name:
            raise Exception(f"save_unit_config: 'unit_name' cannot be None")
        if not unit_conf:
            raise Exception(f"save_unit_config: 'unit_conf' cannot be None")

        common_conf = self.db['units'].find_one({'name': 'common'})
        del common_conf['_id']
        difference = deep_dict_difference(common_conf, unit_conf)
        saved_power_switch_network = difference['power_switch']['network']
        del difference['power_switch']['network']
        del difference['name']

        if not deep_dict_is_empty(difference):
            difference['name'] = unit_name
            difference['power_switch']['network'] = saved_power_switch_network
            try:
                self.db['units'].update_one({'name': unit_name}, {'$set': difference}, upsert=True)
            except PyMongoError:
                logger.error(f"save_unit_config: failed to update unit config for {unit_name=} with {difference=}")

    @cached(cache)
    def get_sites(self) -> dict:
        ret = {}
        for doc in self.db['sites'].find():
            ret[doc['name']] = {
                'deployed': doc['deployed'],
                'planned': doc['planned']
            }
        return ret

    @cached(cache)
    def get_service(self, service_name: str):
        try:
            doc = self.db['services'].find_one({'name': service_name})
        except PyMongoError as e:
            logger.error(f"could not get 'services' (error={e})")
            raise
        return doc

    @cached(cache)
    def get_user(self, name: str = None) -> dict:
        try:
            user = self.db['users'].find_one({'name': name})
            groups: list = user['groups']
        except PyMongoError:
            logger.error(f"failed to get user {name=}")
            raise
        groups.append('everybody')

        collection = self.db['groups']
        # Define the aggregation pipeline
        pipeline = [
            {'$match': {'name': {'$in': groups}}},
            {'$unwind': '$capabilities'},
            {'$group': {'_id': None, 'allCapabilities': {'$addToSet': '$capabilities'}}},
            {'$project': {'_id': 0, 'allCapabilities': 1}},
            {'$unwind': '$allCapabilities'},
            {'$sort': {'allCapabilities': 1}},
            {'$group': {'_id': None, 'sortedCapabilities': {'$push': '$allCapabilities'}}},
            {'$project': {'_id': 0, 'sortedCapabilities': 1}}
        ]

        # Perform the aggregation
        result = list(collection.aggregate(pipeline))

        # Extract the list of all capabilities
        capabilities = []
        if result:
            capabilities = result[0]['sortedCapabilities']

        return {
            'name': name,
            'groups': groups,
            'capabilities': capabilities
        }

    @cached(cache)
    def get_users(self) -> List[str]:
        users = []
        for user in self.db['users'].find():
            users.append(user['name'])
        return users

    def get_service(self, service_name: str) -> dict:
        try:
            service = self.db['services'].find_one({'name': service_name})
        except PyMongoError:
            logger.error(f"could not find_one() for {service_name=}")
            raise
        return service


if __name__ == '__main__':
    cfg = Config()
    print(f"sites: {cfg.get_sites()}")
    us = cfg.get_users()
    for u in us:
        print(f"User '{u}': {cfg.get_user(u)}")
