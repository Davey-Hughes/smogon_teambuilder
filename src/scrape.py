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

from pprint import pprint
import threading
import multiprocessing
import queue
import re

from bs4 import BeautifulSoup
from selenium import webdriver
import pandas as pd

dex_url = 'https://raw.githubusercontent.com/veekun/pokedex/master/pokedex/data/csv/pokemon.csv'
base_url = 'https://www.smogon.com/dex/sm/pokemon/'

poke_queue = queue.Queue()

poke_data = dict()
poke_data_lock = threading.Lock()


def thread_work():
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
        process_poke_tiers(poke, tiers, driver)

        poke_queue.task_done()


def process_poke_tiers(poke, tiers, driver):
    poke_data_lock.acquire()
    poke_data[poke] = dict()
    poke_data_lock.release()

    for k in tiers:
        url = 'https://www.smogon.com/dex/sm/pokemon/' + poke + '/' + k.lower().replace(' ', '_') + '/'
        driver.get(url)
        soup = BeautifulSoup(driver.page_source, 'html.parser')

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
                    key = move_name.find(re.compile(r'.'),
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

            # text for moveset
            text_section = m.find('section')
            headers = text_section.findAll('h1')
            ps = text_section.findAll('p')

            text_dict = dict(zip([h.text for h in headers], [p.text for p in ps]))

            moveset_dict = {
                'move_list': move_list,
                'item': '/'.join(item_list),
                'ability': '/'.join(ability_list),
                'nature': '/'.join(nature_list),
                'evs': '/'.join(ev_list),
                'text_dict': text_dict

            }

            moveset_list.append(moveset_dict)

        poke_data_lock.acquire()
        poke_data[poke][k] = moveset_list
        poke_data_lock.release()


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


def main():
    df = pd.read_csv(dex_url)

    for i, poke in enumerate(df['identifier']):
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
        poke_queue.put(None)

    # wait for threads to finish
    for t in threads:
        t.join()

    # print poke_data
    # for k in poke_data:
        # print(k)
        # pprint(poke_data[k])


if __name__ == '__main__':
    main()
