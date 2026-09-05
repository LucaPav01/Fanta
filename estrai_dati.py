#!/usr/bin/env python3
import requests
from bs4 import BeautifulSoup
import csv
import sys

url = "https://www.fantacalcio-online.com/it/asta-fantacalcio-stima-prezzi"

try:
    print("Fetching pagina...")
    response = requests.get(url, timeout=10)
    response.raise_for_status()

    soup = BeautifulSoup(response.content, 'html.parser')

    # Cerca la tabella principale
    table = soup.find('table')

    if not table:
        print("❌ Tabella non trovata nel HTML statico.")
        print("La pagina usa JavaScript. Provo con Selenium...")

        try:
            from selenium import webdriver
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC

            options = webdriver.ChromeOptions()
            options.add_argument('--headless')
            driver = webdriver.Chrome(options=options)

            print("Caricamento pagina con browser...")
            driver.get(url)

            # Attendi che la tabella carichi
            wait = WebDriverWait(driver, 10)
            wait.until(EC.presence_of_all_elements_located((By.TAG_NAME, "tr")))

            # Estrai HTML dopo caricamento JS
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            table = soup.find('table')
            driver.quit()

            if not table:
                print("❌ Tabella ancora non trovata. Struttura pagina diversa del previsto.")
                sys.exit(1)
        except ImportError:
            print("❌ Selenium non installato. Installa con: pip install selenium")
            sys.exit(1)
        except Exception as e:
            print(f"❌ Errore Selenium: {e}")
            sys.exit(1)

    print("Tabella trovata. Estraggo righe...")

    # Estrai header
    headers = []
    thead = table.find('thead')
    if thead:
        for th in thead.find_all('th'):
            headers.append(th.get_text(strip=True))

    if not headers:
        # Se no thead, prendi dalla prima riga
        first_row = table.find('tr')
        if first_row:
            headers = [cell.get_text(strip=True) for cell in first_row.find_all(['th', 'td'])]

    print(f"Header trovati: {headers}")

    # Estrai righe
    rows = []
    tbody = table.find('tbody')
    if tbody:
        for tr in tbody.find_all('tr'):
            cells = [td.get_text(strip=True) for td in tr.find_all('td')]
            if cells:
                rows.append(cells)
    else:
        # Se no tbody, tutte le righe dopo l'header
        for tr in table.find_all('tr')[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all('td')]
            if cells:
                rows.append(cells)

    print(f"Righe estratte: {len(rows)}")

    # Salva CSV
    output_file = "fantacalcio_prezzi.csv"
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

    print(f"✅ Dati salvati in {output_file}")
    print(f"   - Header: {len(headers)} colonne")
    print(f"   - Righe: {len(rows)} giocatori")

except requests.RequestException as e:
    print(f"❌ Errore request: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Errore: {e}")
    sys.exit(1)
