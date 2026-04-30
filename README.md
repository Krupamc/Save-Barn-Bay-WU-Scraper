# Weather Underground Station Status Check:

* This program checks if a station is online, offline, or in a error state. 
  * If a station is off (configerable) 3 times in a row, an email will be sent saying it went down.
  * If a station is online, nothing happens.
  * If a station is in a error/unknown state, it will not be counted in the 3 times to be off.

---

## Emails:
* The example station_ex.json file shows a configeration where all shutdowns will be sent to yyy@yyyy.com, but you can configure it only send emails to certain people depending on the station.
---

## Future:
* Other notification methods will be added if asked
---

### Delete the wu_staion_state.json before use: New one will be generated.
---

### Packages used:
* requests:
  * Basic Web Curling.
* beautifulsoup4
  * Webscraping 
* playwright
  * Headless browser

#### Commands for packages:
* pip install requests beautifulsoup4 playwright
* python -m playwright install
