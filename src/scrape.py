# Scrape pokemon data from smogon
# Copyright (C) 2018  Mingu Kim & David Hughes

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import threading
import multiprocessing
import queue
import re
import argparse
import getpass

from bs4 import BeautifulSoup
from selenium import webdriver
import pandas as pd
import psycopg2

dex_url = 'https://raw.githubusercontent.com/veekun/pokedex/master/pokedex/data/csv/pokemon.csv'
base_url = 'https://www.smogon.com/dex/sm/pokemon/'

poke_queue = queue.Queue()

poke_data = dict()
poke_data_lock = threading.Lock()

args = None


# from https://stackoverflow.com/a/22157136
class SmartFormatter(argparse.HelpFormatter):

    def _split_lines(self, text, width):
        if text.startswith('R|'):
            return text[2:].splitlines()
        # this is the RawTextHelpFormatter._split_lines
        return argparse.HelpFormatter._split_lines(self, text, width)


# gets the strings of paragraphs between two tags
def get_tips(curr_tag):
    return list(map(lambda s: str(s), filter(lambda s: s != '\n', curr_tag.next_siblings)))


# strips HTML tags and does some extra formatting on the string
def sHTML_format(lst):
    if lst is None:
        return None
    else:
        return re.sub('<[^<]+?>', '', ' '.join(lst).replace('\n', '').strip())


# overall controller function for each thread
def thread_work():
    # use chromedriver to process the webpages (since they're in react)
    driver_options = webdriver.chrome.options.Options()
    driver_options.add_argument('headless')
    driver = webdriver.Chrome(options=driver_options)

    while True:
        i, poke = poke_queue.get()

        if poke is None:
            driver.quit()
            break

        print('Getting data for %d, %s' % (i + 1, poke))

        poke_soup = get_poke_soup(poke, driver)
        tiers = get_poke_tiers(poke, poke_soup)

        # skip this pokemon if it doesn't compete in any tiers
        if tiers != {}:
            process_poke_tiers(poke, tiers, driver)

        poke_queue.task_done()


def process_poke_tiers(poke, tiers, driver):
    poke_data_lock.acquire()
    poke_data[poke] = dict()
    poke_data_lock.release()

    for tier in tiers:
        url = 'https://www.smogon.com/dex/sm/pokemon/' + poke + '/' + \
               tier.lower().replace(' ', '_') + '/'

        driver.get(url)
        soup = BeautifulSoup(driver.page_source, 'html.parser')

        checks_counters = None

        movesets = soup.findAll(
            re.compile(r'.'),
            attrs={
                'data-reactid': re.compile(r'\.0\.1\.1\.3\.6\.0\.2\.2:[0-9]$')
            }
        )

        moveset_list = []

        for m in movesets:
            # name of the moveset
            moveset_name = m.find('h1').text

            # moves from moveset
            moves = m.findAll(class_='MoveList')
            movedict = dict()
            for move in moves:
                for move_name in move:
                    key = move_name.find(
                        re.compile(r'.'),
                        attrs={
                            'data-reactid': re.compile(r'.')
                        }
                    )['data-reactid'].split('$')[0]

                    if key in movedict:
                        movedict[key] = movedict[key] + '/' + move_name.text
                    else:
                        movedict[key] = move_name.text

            move_list = [movedict[k] for k in movedict]

            # item from moveset
            item_list = []
            items = m.findAll(class_='ItemList')
            for item in items:
                li = item.findAll('li')
                for l in li:
                    item_list.append(l.text)

            # ability from moveset
            ability_list = []
            abilities = m.findAll(class_='AbilityList')
            for ability in abilities:
                li = ability.findAll('li')
                for l in li:
                    ability_list.append(l.text)

            # natures from moveset
            nature_list = []
            natures = m.findAll(class_='NatureList')
            for nature in natures:
                li = nature.findAll('li')
                for l in li:
                    nature_list.append(l.text)

            # evs from moveset
            ev_list = []
            evs = m.findAll(class_='evconfig')
            for ev in evs:
                li = ev.findAll('li')
                for l in li:
                    ev_list.append(l.text)

            # texts for moveset
            text_soup = m.find('section')

            # we only care about these
            #
            # TODO: could potentially add a 'misc' column to the db table and
            # concatenate all other header paragraphs into one
            texts = {
                'Moves': None,
                'Set Details': None,
                'Usage Tips': None,
                'Team Options': None,
            }

            for tag in text_soup.findAll('h1'):
                texts[tag.text] = get_tips(tag)

            texts = dict(
                map(lambda k: (k, sHTML_format(texts[k])), texts)
            )

            moveset_dict = {
                'moveset_name': moveset_name,
                'move_list': move_list,
                'item': '/'.join(item_list).strip(),
                'ability': '/'.join(ability_list),
                'nature': '/'.join(nature_list),
                'evs': '/'.join(ev_list),
                'text': texts
            }

            moveset_list.append(moveset_dict)

        # options for each tier
        options_soup = soup.find(
            re.compile(r'.'),
            attrs={'data-reactid': '.0.1.1.3.6.0.2.3'}
        )

        checks_counters = None
        other_options = None

        for tag in options_soup.findAll('h1'):
            if tag.text == 'Checks and Counters':
                checks_counters = sHTML_format(get_tips(tag))
            elif tag.text == 'Other Options':
                other_options = sHTML_format(get_tips(tag))

        poke_data_lock.acquire()
        poke_data[poke][tier] = dict()
        poke_data[poke][tier]['moveset_list'] = moveset_list
        poke_data[poke][tier]['checks_counters'] = checks_counters
        poke_data[poke][tier]['other_options'] = other_options
        poke_data_lock.release()


# find all the tiers for a pokemon's page
def get_poke_tiers(poke, soup):
    formats = soup.findAll(
        re.compile(r'.'),
        attrs={
            'data-reactid':
            re.compile(r'\.0\.1\.1\.3\.6\.0\.0\.2\.[0-9]\.0')
        }
    )

    tiers = {f.text: dict() for f in formats}

    return tiers


def get_poke_soup(poke, driver):
    full_url = base_url + poke + '/'

    driver.get(full_url)
    soup = BeautifulSoup(driver.page_source, 'html.parser')

    return soup


# create the tables in the database if they don't already exist
def create_tables(cur):
    cur.execute('SELECT to_regclass(%s)', ('public.movesets',))
    if cur.fetchone() != ('movesets',):
        cur.execute(
            'CREATE TABLE movesets (\
                poke_name varchar,\
                tier varchar,\
                moveset_name varchar,\
                move_list varchar[],\
                item varchar,\
                ability varchar,\
                nature varchar,\
                evs varchar,\
                moves varchar,\
                set_details varchar,\
                usage_tips varchar,\
                team_options varchar,\
                PRIMARY KEY(poke_name, tier, moveset_name)\
            )'
        )

    cur.execute('SELECT to_regclass(%s)', ('public.tier_options',))
    if cur.fetchone() != ('tier_options',):
        cur.execute(
            'CREATE TABLE tier_options (\
                poke_name varchar,\
                tier varchar,\
                other_options varchar,\
                checks_counters varchar,\
                PRIMARY KEY(poke_name, tier)\
            )'
        )


# insert the data from the poke_data dictionary
def insert_data(cur, poke_data):
    for poke_name in poke_data:
        for tier in poke_data[poke_name]:
            if args.force_update:
                cur.execute(
                    'INSERT INTO public.tier_options VALUES (%s, %s, %s, %s)\
                    ON CONFLICT (poke_name, tier) DO UPDATE \
                    SET poke_name = excluded.poke_name,\
                        tier = excluded.tier,\
                        other_options = excluded.other_options,\
                        checks_counters = excluded.checks_counters',
                    (poke_name, tier,
                     poke_data[poke_name][tier]['other_options'],
                     poke_data[poke_name][tier]['checks_counters'])
                )
            else:
                cur.execute(
                    'INSERT INTO public.tier_options VALUES (%s, %s, %s, %s)\
                    ON CONFLICT DO NOTHING',
                    (poke_name, tier,
                     poke_data[poke_name][tier]['other_options'],
                     poke_data[poke_name][tier]['checks_counters'])
                )

            if 'moveset_list' not in poke_data[poke_name][tier]:
                continue

            for ms in poke_data[poke_name][tier]['moveset_list']:
                if args.force_update:
                    cur.execute(
                        'INSERT INTO public.movesets VALUES\
                            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)\
                            ON CONFLICT (poke_name, tier, moveset_name) \
                            DO UPDATE \
                            SET poke_name = excluded.poke_name,\
                                tier = excluded.tier,\
                                moveset_name = excluded.moveset_name ,\
                                move_list = excluded.move_list,\
                                item = excluded.item,\
                                ability = excluded.ability,\
                                nature = excluded.nature,\
                                evs = excluded.evs,\
                                moves = excluded.moves,\
                                set_details = excluded.set_details,\
                                usage_tips = excluded.usage_tips,\
                                team_options = excluded.team_options',
                        (poke_name, tier, ms['moveset_name'], ms['move_list'],
                         ms['item'], ms['ability'], ms['nature'], ms['evs'],
                         ms['text']['Moves'], ms['text']['Set Details'],
                         ms['text']['Usage Tips'], ms['text']['Team Options'])
                    )
                else:
                    cur.execute(
                        'INSERT INTO public.movesets VALUES\
                            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)\
                            ON CONFLICT DO NOTHING',
                        (poke_name, tier, ms['moveset_name'], ms['move_list'],
                         ms['item'], ms['ability'], ms['nature'], ms['evs'],
                         ms['text']['Moves'], ms['text']['Set Details'],
                         ms['text']['Usage Tips'], ms['text']['Team Options'])
                    )


# check which pokemon are already in the database
# (does not guarantee completeness of the data for each pokemon)
def select_pokemon_names(cur):
    cur.execute('SELECT DISTINCT poke_name FROM public.movesets')
    return set([poke_name[0] for poke_name in cur.fetchall()])


def connect_to_db():
    # try without supplying password
    try:
        conn = psycopg2.connect('dbname=%s user=%s' %
                                (args.dbname, args.role,))
        return conn
    except psycopg2.OperationalError as e:
        if 'no password supplied' not in str(e):
            raise

    # try again asking for password
    try:
        conn = psycopg2.connect('dbname=%s user=%s password=%s' %
                                (args.dbname, args.role,
                                 getpass.getpass(prompt='DB Password: ')))
        return conn
    except (NameError, psycopg2.OperationalError):
        raise


# parse command line arguments
def parse_arguments():
    parser = argparse.ArgumentParser(formatter_class=SmartFormatter)
    parser.add_argument('--dbname', help='name of the database to connect to')
    parser.add_argument('--role', help='role to access this database with')

    parser.add_argument(
        '--dex-path',
        help='R|read a custom csv file for pokemon to scrape;\n' +
             'can be a local file or url;\n' +
             'use column header "identifier" for pokemon names, default is:\n' +
             '%s' % (dex_url),
        default=dex_url
    )

    parser.add_argument(
        '--skip-in-db',
        help='R|skip scraping a pokemon if it is already in the database;\n' +
             'takes precidence over --force-update',
        action='store_true',
        default=False
    )

    parser.add_argument(
        '--force-update',
        help='use scraped data for any conflicts in database',
        action='store_true',
        default=False
    )

    global args
    args = parser.parse_args()


def main():
    parse_arguments()

    # connect to the db
    conn = connect_to_db()
    cur = conn.cursor()

    create_tables(cur)
    conn.commit()

    # read the csv of pokemon pages to scrape
    df = pd.read_csv(args.dex_path)

    # skip pokemon that are already in the db
    if args.skip_in_db:
        old_names = select_pokemon_names(cur)
        all_names = set(df['identifier'])
        names = all_names.difference(old_names)
    else:
        names = df['identifier']

    # ready the queue for multithreading work
    for i, poke in enumerate(names):
        poke_queue.put((i, poke))

    num_threads = multiprocessing.cpu_count()
    threads = []

    # launch threads
    for i in range(num_threads):
        thread = threading.Thread(target=thread_work)
        thread.start()
        threads.append(thread)

    # wait for all pokemon to be processed
    poke_queue.join()

    # tell all threads to exit:
    for i in range(num_threads):
        poke_queue.put((None, None))

    # wait for threads to finish
    for t in threads:
        t.join()

    # insert all the data into the db
    insert_data(cur, poke_data)

    conn.commit()
    cur.close()
    conn.close()


if __name__ == '__main__':
    main()
