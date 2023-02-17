#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""stockmine.py - main driver program of stockmine.

See README.md or https://github.com/nicholasadamou/stockmine
for more information.

Copyright (C) Nicholas Adamou 2019
stockmine is released under the Apache 2.0 license. See
LICENSE for the full license text.
"""

import os
import argparse
import re
import sys
import time
from time import sleep
from datetime import datetime
from os import getenv

import nltk as nltk
from py_dotenv import read_dotenv
from pyfiglet import Figlet

from headlinelistener import HeadlineListener
from logs import *
from analysis import Analysis
from monitor import Monitor
from twitter import Twitter
from yahoo import scrap_company_data

stockmine_VERSION = '0.1a'
__version__ = stockmine_VERSION

if sys.version_info >= (3, 0):
    unicode = str

# Download the 'punkt' package for NLTK
# for tokenizing tweet text.
nltk.download('punkt', quiet=True)

# Read API keys
try:
    read_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except FileNotFoundError:
    print("\n%s '.env' does not exist. Please create the file & add the necessary API keys to it." % ERROR)
    exit(1)

# The keys for the Twitter app we're using for API requests
# (https://apps.twitter.com/app/13239588). Read from environment variables.
TWITTER_CONSUMER_KEY = getenv('TWITTER_CONSUMER_KEY')
TWITTER_CONSUMER_SECRET = getenv('TWITTER_CONSUMER_SECRET')

# The keys for the Twitter account we're using for API requests.
# Read from environment variables.
TWITTER_ACCESS_TOKEN = getenv('TWITTER_ACCESS_TOKEN')
TWITTER_ACCESS_TOKEN_SECRET = getenv('TWITTER_ACCESS_TOKEN_SECRET')

# The duration of the smallest backoff step in seconds.
BACKOFF_STEP_S = 0.1

# The maximum number of retry steps, equivalent to 0.1 * (2^12 - 1) = 409.5
# seconds of total delay. This is the largest interval that one backoff
# sequence may take.
MAX_TRIES = 12

# The time in seconds after which to reset a backoff sequence. This is the
# smallest interval at which backoff sequences may repeat normally.
BACKOFF_RESET_S = 30 * 60

# The file-name of the outputted .csv file
FILE_NAME = 'stockmine' + "_" + time.strftime("%Y%m%d-%H%M%S") + ".csv"


class Main:
    """A wrapper for the main application logic and retry loop."""

    def __init__(self, args):
        self.twitter = Twitter()
        self.args = args

    def twitter_callback(self, tweet):
        """Analyzes tweets"""

        # Start analysis.
        analysis = Analysis()

        # create tokens of words in text using NLTK.
        text_for_tokens = re.sub(r"[%|$.,!:@]|\(|\)|#|\+|(``)|('')|\?|-", "", tweet['text'])
        tokens = nltk.word_tokenize(text_for_tokens)
        print(f"{OK} NLTK Tokens: {str(tokens)}")

        # Skip if required NLTK tokens are not present within the tweet's body.
        if args.required_keywords:
            required_keywords = args.required_keywords.split(',')
            if all(token not in required_keywords for token in tokens):
                print(f"{WARNING} Tweet does not contain required NLTK tokens, skipping.")
                return

        # Skip if ignored NLTK tokens are present within the tweet's body.
        if args.ignored_keywords:
            ignored_keywords = args.ignored_keywords.split(',')
            if any(token in ignored_keywords for token in tokens):
                print(f"{WARNING} Tweet contains an ignored NLTK token, skipping.")
                return

        # strip out hash-tags for language processing.
        text = re.sub(r"[#|@$]\S+", "", tweet['text']).strip()
        tweet['text'] = text
        print(f"{OK} Strip Hash-tags from text: {tweet['text']}")

        # Find any mention of companies in tweet.
        companies = analysis.find_companies(tweet)

        if not companies:
            print(
                f"{ERROR} Didn't find any mention to any known publicly traded companies."
            )
            return

        # Analyze a tweet & obtain its sentiment.
        results = analysis.analyze(companies)

        # Write results to [.csv] file.
        print('\n%s Writing results to %s' % (WARNING, FILE_NAME))
        f = open(FILE_NAME, "a")

        # Write fields to [.csv]
        fields = ['symbol', 'name', 'sentiment', 'opinion', 'tweet', 'url']
        if ",".join(fields) not in open(FILE_NAME).read():
            print(f"{OK} fields: {fields}")
            f.write(",".join(fields) + "\n")

        # Write individual rows to [.csv].
        for company in companies:
            # Extract individual row data
            symbol = company['symbol']
            name = company['name']
            tweet = company['tweet']
            url = company['url']
            data = [str(e) for e in results[company['symbol']].values()]

            # Construct individual row.
            row = f"{symbol},{name}," + ",".join(data) + "," + tweet + "," + url

            # Write row data to [.csv].
            print(f"{OK} row: {row}")
            f.write(row + "\n")

        print(f"{OK} {results}")

    def run_session(self, args):
        """Runs a single streaming session. Logs and cleans up after
        exceptions.
        """

        print(f"{WARNING} Starting new session.")
        self.twitter.start_streaming(args, self.twitter_callback)

    def backoff(self, tries):
        """Sleeps an exponential number of seconds based on the number of
        tries.
        """

        delay = BACKOFF_STEP_S * pow(2, tries)
        print("%s Waiting for %.1f seconds." % (WARNING, delay))
        sleep(delay)

    def run(self):
        """Runs the main retry loop with exponential backoff."""

        tries = 0
        while True:

            # The session blocks until an error occurs.
            self.run_session(self.args)

            # Remember the first time a backoff sequence starts.
            now = datetime.now()
            if tries == 0:
                print(f"{WARNING} Starting first backoff sequence.")
                backoff_start = now

            # Reset the backoff sequence if the last error was long ago.
            if (now - backoff_start).total_seconds() > BACKOFF_RESET_S:
                print(f"{OK} Starting new backoff sequence.")
                tries = 0
                backoff_start = now

            # Give up after the maximum number of tries.
            if tries >= MAX_TRIES:
                print(f"{WARNING} Exceeded maximum retry count.")
                break

            # Wait according to the progression of the backoff sequence.
            self.backoff(tries)

            # Increment the number of tries for the next error.
            tries += 1


if __name__ == "__main__":
    # Print banner and app description
    custom_fig = Figlet(font='slant')
    print(custom_fig.renderText('stockmine'))
    print("Crowd-sourced stock analyzer and stock predictor using\n"
          "Google Natural Language Processing API, Twitter, and\n"
          "Wikidata API in order to determine, if at all, how much\n"
          "emotions can affect a stock price?\n")

    # parse CLI arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("-k", "--keywords", metavar="KEYWORDS",
                        help="Use keywords to search for in Tweets instead of feeds. "
                             "Separated by comma, case insensitive, spaces are ANDs commas are ORs. "
                             "Example: TSLA,'Elon Musk',Musk,Tesla,SpaceX")
    parser.add_argument("--required-keywords", metavar="REQUIRED_KEYWORDS",
                        help="Words that each tweet from a user's feed must contain. "
                             "Separated by comma, case insensitive. "
                             "Example: Tesla,@Tesla,#Tesla,tesla,TSLA,tsla,#TSLA,#tsla,'elonmusk',Elon,Musk")
    parser.add_argument("--ignored-keywords", metavar="IGNORED_KEYWORDS",
                        help="Words that each tweet must not contain. "
                             "Can be used with feeds or keywords. "
                             "Separated by comma, case insensitive, spaces are ANDs commas are ORs. "
                             "Example: win,Win,giveaway,Giveaway")
    parser.add_argument("-f", "--file", metavar="FILE",
                        help="Use Twitter User IDs from file.")
    parser.add_argument("-u", "--url", metavar="URL",
                        help="Scrap Twitter User IDs from URL.")
    parser.add_argument("-s", "--symbol", metavar="SYMBOL",
                        help="Stock symbol to use when fetching stock data., example: TSLA")
    parser.add_argument("--news-headlines", action="store_true",
                        help="Get news headlines instead of Twitter using stock symbol, example: TSLA")
    parser.add_argument("--frequency", metavar="FREQUENCY", default=120, type=int,
                        help="How often in seconds to retrieve news headlines. (default: 120 sec)")
    parser.add_argument("--follow-links", action="store_true",
                        help="Follow links on news headlines and scrape relevant text from landing page.")
    parser.add_argument("-V", "--version", action="version",
                        version="stockmine v%s" % stockmine_VERSION,
                        help="Prints version and exits.")
    args = parser.parse_args()

    # Print help if no arguments are given.
    if len(sys.argv) == 1:
        parser.print_help()

    # Handle CLI arguments

    # python3 stockmine.py -k TSLA,'Elon Musk',Musk,Tesla,SpaceX
    # python3 stockmine.py -f users.txt
    if args.keywords or args.file or args.url:
        # Make sure the correct arguments are passed.
        if args.news_headlines or args.follow_links or args.symbol:
            print("%s Arguments [NEWS-HEADLINES, SYMBOL, or FOLLOW-LINKS] cannot be used with argument(s) [KEYWORDS, "
                  "FILE, or URL]" % ERROR)
            exit(1)

        print("%s TWITTER_CONSUMER_KEY = %s" % (OK, TWITTER_CONSUMER_KEY))
        print("%s TWITTER_CONSUMER_SECRET = %s" % (OK, TWITTER_CONSUMER_SECRET))
        print("%s TWITTER_ACCESS_TOKEN = %s" % (OK, TWITTER_ACCESS_TOKEN))
        print("%s TWITTER_ACCESS_TOKEN_SECRET = %s" % (OK, TWITTER_ACCESS_TOKEN_SECRET))
        print()

        if args.keywords:
            print("%s KEYWORDS: %s" % (OK, args.keywords))

        if args.file:
            print("%s FILE: %s" % (OK, args.file))

        if args.url:
            print("%s URL: %s" % (OK, args.url))

        if args.required_keywords and args.ignored_keywords:
            print("%s REQUIRED_KEYWORDS = %s" % (OK, args.required_keywords))
            print("%s IGNORED_KEYWORDS = %s" % (OK, args.ignored_keywords))
            print()

        if args.required_keywords and not args.ignored_keywords:
            print("%s REQUIRED_KEYWORDS = %s" % (OK, args.required_keywords))
            print()

        if args.ignored_keywords and not args.required_keywords:
            print("%s IGNORED_KEYWORDS = %s" % (OK, args.ignored_keywords))
            print()

        if args.keywords and args.required_keywords:
            print("%s KEYWORDS and REQUIRED_KEYWORDS cannot be used in tandom." % ERROR)
            exit(1)

        monitor = Monitor()
        monitor.start()

        try:
            Main(args=args).run()
        finally:
            monitor.stop()
    else:
        # python3 stockmine.py --symbol TSLA
        if args.symbol and not args.news_headlines:
            symbol = args.symbol

            print("%s SYMBOL = %s" % (OK, symbol))

            results = scrap_company_data(symbol)
            print("%s FOUND DATA for %s: %s" % (OK, symbol, results))

            # Write results to [.csv] file.
            print('\n%s Writing results to %s' % (WARNING, FILE_NAME))
            f = open(FILE_NAME, "w")

            # Write fields to [.csv]
            fields = ['symbol', 'date', 'last price', 'low', 'high', 'volume']
            if ",".join(fields) not in open(FILE_NAME).read():
                print("%s fields: %s" % (OK, fields))
                f.write(",".join(fields) + "\n")

            # Write row data to [.csv].
            row = ",".join([str(e) for e in results.values()]) + "\n"
            print("%s row: %s" % (OK, row))
            f.write(row)

        # python3 stockmine.py --news-headlines --follow-links --symbol TSLA --frequency 120
        elif args.symbol and args.news_headlines and args.follow_links:
            symbol = args.symbol
            frequency = args.frequency

            print("%s SYMBOL = %s" % (OK, symbol))
            print("%s NEWS-HEADLINES = %s" % (OK, args.news_headlines))
            print("%s FOLLOW-LINKS = %s" % (OK, args.follow_links))
            print("%s FREQUENCY = %s sec." % (OK, args.frequency))
            print()

            try:
                news_listener = HeadlineListener(args=args, symbol=symbol, frequency=frequency, follow_links=args.follow_links)
            except KeyboardInterrupt:
                print("%s Ctrl-c keyboard interrupt, exiting." % WARNING)
                sys.exit(0)

        # python3 stockmine.py --news-headlines --symbol TSLA --frequency 120
        elif args.symbol and args.news_headlines and not args.follow_links:
            symbol = args.symbol
            frequency = args.frequency

            print("%s SYMBOL = %s" % (OK, symbol))
            print("%s NEWS-HEADLINES = %s" % (OK, args.news_headlines))
            print("%s FOLLOW-LINKS = %s" % (OK, args.follow_links))
            print("%s FREQUENCY = %s sec." % (OK, args.frequency))
            print()

            try:
                news_listener = HeadlineListener(args=args, symbol=symbol, frequency=frequency)
            except KeyboardInterrupt:
                print("%s Ctrl-c keyboard interrupt, exiting." % WARNING)
                sys.exit(0)
