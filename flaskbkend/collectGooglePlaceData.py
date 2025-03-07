import pandas as pd
import requests
from sqlalchemy.engine import create_engine
import numpy as np
from datetime import datetime
import logging
import re
import json
import os, sys


#import user define classes, and keys
from ORM.orm import Gym, Photo, Review, Session, loadConfigs
from ORM.keys import GKEY

#adds logging for sqlalchemy engine
logging.basicConfig()
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

#safely splits up column with list

def getFirstItem(gym_info: list):
    try:
        return gym_info[0]
    except:
        return ""

def getSecondItem(gym_info: list):
    try:
        return gym_info[1]
    except:
        return ""

#grab non-nested values with try-except
def tryToGet(searchResults, resultKey: str, isList: bool = False):
    try:
        return searchResults[resultKey]
    except:
        if not isList:
            return ""
        else:
            return []

def loadGymDf(filepath: str = 'data.json'):
    #load the scrapped data from mountainProject(mP)
    df = pd.read_json(filepath, orient='index')

    # split up list column
    df['gymURL'] = df['gym_info'].map(getFirstItem)
    df['gym_address'] = df['gym_info'].map(getSecondItem)
    del df['gym_info']

    #create empty columns to fill
    df["nameFromGoogle"] = ""
    df["google_Place_ID"] = ""
    df["locLat"] = ""
    df["locLong"] = ""
    df["business_status"] = ""
    df["google_photoReferences"] = ""
    df["googleRating"] = ""
    df["numUsersRated"] = ""
    df["typeList"] = ""

    return df

if __name__ == "__main__":
    #load gym dataframe
    df = loadGymDf()

    #specify the url endpoint, and fields to request
    placeTextSearchURL = 'https://maps.googleapis.com/maps/api/place/textsearch/json'
    fields = [
            "place_id",
            "business_status",
            "rating",
            "types",
            "user_ratings_total",
            "name",
            "geometry",
            'photos'
        ]
    #For testing perposes, script will be run on just Delaware's 4 gyms
    #df = df[df['state'] == 'Delaware'] #this line must be removed when done testing

    #load configs and initialize sqlalchemy session
    config = loadConfigs()
    engine = create_engine(**config)
    Session.configure(bind=engine)
    session = Session()

    for idx, row in df.iterrows():
        

        checkRecord = session.query(Gym).filter(Gym.gymName == row['locName']).first()
        if checkRecord is not None:
            continue

        #assign address to variable, and create params dict
        address = row['locName'].strip('…') + " " + row['gym_address']
        
        params = {
                    "key": GKEY,
                    "query": address,
                    'fields':fields
                }
        #get request to location text search api endpoint
        searchResults = requests.get(placeTextSearchURL, params=params)
        
        #conv to dict for easy extract
        searchResults = searchResults.json()
        
        #if results are empty, attmept a second search with just name
        if not searchResults["results"]:
            params.update({'query': row['locName'].strip('…')})
            searchResults = requests.get(placeTextSearchURL, params=params).json()
        
        if searchResults["results"]:
            searchResults = searchResults["results"][0]

        # extract non-nested value
        df.loc[idx,"google_Place_ID"] = tryToGet(searchResults,  "place_id")
        df.loc[idx,"business_status"] = tryToGet(searchResults,  "business_status")
        df.loc[idx,"googleRating"] = tryToGet(searchResults,"rating")
        df.at[idx,"typeList"] = tryToGet(searchResults, "types", True)
        df.loc[idx,"numUsersRated"] = tryToGet(searchResults, "user_ratings_total")
        df.loc[idx, "nameFromGoogle"] = tryToGet(searchResults, "name")

        
        #manual try blocks for nested values
        try:
            df.loc[idx,"locLat"] = searchResults["geometry"]["location"]["lat"]
            df.loc[idx,"locLong"] = searchResults["geometry"]["location"]["lng"]
        except:
            df.loc[idx,"locLat"] = None
            df.loc[idx,"locLong"] = None

        #create orm-gym object and add to session
        gymRecord = df.loc[idx]
        recordToInsert = Gym(gymName = gymRecord['locName'],
            gymNameFromGoogle = gymRecord['nameFromGoogle'],
            gymAddress = gymRecord['gym_address'],
            gymState = gymRecord['state'],
            isOperational = gymRecord['business_status'],
            locLatitude = gymRecord["locLat"],
            locLongitude = gymRecord["locLong"],
            ratingFromGoogle = gymRecord["googleRating"],
            numGoogleUsersRated = gymRecord["numUsersRated"],
            googlePlaceID = gymRecord["google_Place_ID"],
            typeListStr = ", ".join(gymRecord["typeList"])
        )
        session.add(recordToInsert)
        
        #add img_list to DB for this gym
        for img in gymRecord['img_list']:
            #check if value exists, dont add to db if do
            checkRecord = session.query(Photo).filter(Photo.photoURL == img).first()
            if checkRecord is not None:
                continue

            photoRecordToInsert = Photo(photoURL = img, gym = recordToInsert)
            session.add(photoRecordToInsert)

        #naual try blocks for googlephotolist and add to DB
        try:
            photoReferenceList = []
            for photo in searchResults['photos']:
                #check if value exists, dont add to db if do
                checkRecord = session.query(Photo).filter(Photo.photoGoogleReference == photo["photo_reference"]).first()
                if checkRecord is not None:
                    continue

                photoReferenceList.append(photo["photo_reference"])
                photoRecordToInsert = Photo(photoGoogleReference = photo["photo_reference"], gym = recordToInsert)
                session.add(photoRecordToInsert)
            df.at[idx,"google_photoReferences"] = photoReferenceList
            
        except:
            df.at[idx,"google_photoReferences"] = []

        # try and fetch review data using GooglePlaceID, push to db
        try:
            
            googlePlaceID = df.loc[idx, 'google_Place_ID']
            detailsUrl = 'https://maps.googleapis.com/maps/api/place/details/json?'
            detailParams = {
                "key": GKEY,
                "place_id": df.loc[idx,"google_Place_ID"],
                'fields':["reviews"]
            }
            reviewResults = requests.get(detailsUrl, params=detailParams).json()
            for review in reviewResults['result']['reviews']:
                author = tryToGet(review,'author_name')
                rating = tryToGet(review, 'rating')
                content = tryToGet(review,'text')
                time_posted = tryToGet(review,'time')
                reviewToInsert = Review(
                    author=author,
                    content=content,
                    rating=rating,
                    timePosted=datetime.fromtimestamp(time_posted),
                    source='Google',
                    gym=recordToInsert
                )
                session.add(reviewToInsert)
            
        except:
            pass

        #commit gym and all items attached to that gym
        session.commit()

    session.close()