from pymongo import MongoClient
import argparse
from bson.binary import Binary
import os
from config import WEIZMANN_DOMAIN


def store_user_picture(site: str, user: str, img: str):
    client = MongoClient(f"mongodb://mast-{site}-control.{WEIZMANN_DOMAIN}:27017/")
    db = client['mast']
    collection = db['users']

    # Read the JPEG file as binary data
    with open(img, 'rb') as f:
        img_data = f.read()

    # Convert the binary data to BSON binary data
    bson_data = Binary(img_data)

    # Update the user's picture
    result = collection.update_one(
        {'name': user},
        {'$set': {'picture': bson_data}},
        upsert=True
    )

    if result.matched_count > 0:
        print(f"Updated picture for user: {user}")
    elif result.upserted_id:
        print(f"Inserted new user with picture: {user}")
    else:
        print(f"No changes made for user: {user}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Store a user's picture in MongoDB.")
    parser.add_argument('--user', required=True, help="The username.")
    parser.add_argument('--site', required=False, default='wis', help="The MAST site (wis, ns).")
    parser.add_argument('--img', required=True, help="The image filename (JPEG).")

    args = parser.parse_args()

    if args.site not in ['wis', 'ns']:
        raise Exception(f"site must be one of ['wis', 'ns']")
    if not os.access(args.img, os.R_OK):
        raise Exception(f"no read access to {args.img=}")

    store_user_picture(args.site, args.user, args.img)
