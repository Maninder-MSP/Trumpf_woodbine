#!/usr/bin/env python3
# encoding: utf-8

# Flex-ESS MongoDB Class by S. Coates
# Copyright Multi Source Power 2021
# www.multisourcepower.com

# TinyDB (because MongoDB takes up too much storage and we now don't need pyMongo)

from tinydb import TinyDB, Query
import time


class FlexTinyDB:
    def __init__(self):
        self.flex_db = TinyDB("db.json")

    def save_to_db(self, name, data):

        # Check there isn't a write in progress!

        item = Query()

        # Remove entry before adding it again
        res = self.flex_db.search(item.key == name)

        if len(res) > 0:
            for record in res:
                self.flex_db.remove(item.key == name)

        print("Database Updated")
        self.flex_db.insert({"key": name, "value": data})

    def del_from_db(self):
        pass

    def fetch_from_db(self, key):

        item = Query()
        item = self.flex_db.get(item.key == key)

        if item is not None:
            return item.get("value")
