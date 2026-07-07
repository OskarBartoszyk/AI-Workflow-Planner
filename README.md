# Plan projektu: AI Workflow Planner

## Cel produktu

Zbudować aplikację, która:

* przyjmuje zadanie w języku naturalnym albo w JSON,
* zamienia je na plan wykonania,
* pokazuje ten plan jako graf,
* uruchamia taski przez workerów,
* zapisuje wyniki i historię,
* później pozwala podmienić planner na lokalny model z Ollamy.
  Ollama udostępnia lokalne API pod `localhost:11434`, a LangChain i LangGraph są dziś rozwijane jako warstwa do budowy agentów i workflow opartych o grafy. Pydantic traktujemy jako warstwę modeli danych, walidacji i serializacji.

## Zakres produktu

Nie budujemy „AI do wszystkiego”. Budujemy:

* **planner**,
* **DAG / workflow engine**,
* **worker system**,
* **wizualizację grafu**,
* **historię uruchomień**,
* **lokalne modele AI jako opcjonalny komponent**.

---

# 21-dniowy plan

## Dzień 1 — Definicja produktu i granic

* [ ] Nazwa projektu i opis w 2–3 zdaniach
* [ ] Lista problemów, które produkt rozwiązuje
* [ ] Lista rzeczy, których produkt nie robi
* [ ] Ustalenie 3 głównych użytkowników docelowych
* [ ] Założenie repozytorium i struktury notatek

**Kamień milowy:** jasno określony zakres produktu, bez rozlania się na „AI do wszystkiego”.

**Notatki:**

```text


```

---

## Dzień 2 — Model danych produktu

* [ ] Zdefiniowanie podstawowych encji: Task, Node, Edge, Workflow, Run
* [ ] Spisanie pól, które będą potrzebne w każdej encji
* [ ] Ustalenie, co będzie obowiązkowe, a co opcjonalne
* [ ] Pierwsza wersja schematów danych

**Notatki:**

```text


```

---

## Dzień 3 — Pierwsze modele Pydantic

* [ ] Stworzenie modeli danych dla workflow
* [ ] Dodanie walidacji pól
* [ ] Uporządkowanie typów
* [ ] Zapisanie przykładowych danych testowych

**Notatki:**

```text


```

---

## Dzień 4 — Opis działania workflow

* [ ] Zapisanie, jak task przechodzi od inputu do outputu
* [ ] Zdefiniowanie statusów taska
* [ ] Zdefiniowanie statusów całego runa
* [ ] Opis błędu, retry i failure

**Notatki:**

```text


```

---

## Dzień 5 — Minimalny koncept DAG

* [ ] Narysowanie przykładowego grafu ręcznie
* [ ] Ustalenie, jak taski zależą od siebie
* [ ] Zapisanie 3 przykładowych workflowów
* [ ] Opis reguł poprawności grafu

**Notatki:**

```text


```

---

## Dzień 6 — Kontekst wykonania

* [ ] Ustalenie, co jest przekazywane między taskami
* [ ] Ustalenie, co zapisuje się do contextu
* [ ] Ustalenie, co zostaje w pamięci po wykonaniu
* [ ] Spisanie przykładowego przebiegu wykonania

**Notatki:**

```text


```

---

## Dzień 7 — Pierwszy milestone

* [ ] Zapisany kompletny opis MVP
* [ ] Zdefiniowane encje i zależności
* [ ] Gotowa logika workflow na papierze
* [ ] Jasny podział na planner, engine, worker, UI

**Kamień milowy:** masz formalnie opisany produkt, a nie luźny pomysł.

**Notatki:**

```text


```

---

## Dzień 8 — Worker catalog

* [ ] Lista typów workerów, które chcesz mieć w MVP
* [ ] Podział workerów na kategorie
* [ ] Opis wejścia i wyjścia dla każdego workera
* [ ] Wybór 3 workerów startowych

**Notatki:**

```text


```

---

## Dzień 9 — Użytkownik i sposób wprowadzania tasków

* [ ] Opis wejścia tekstowego
* [ ] Opis wejścia JSON
* [ ] Ustalenie prostego formatu komend
* [ ] Lista przykładowych promptów do systemu

**Notatki:**

```text


```

---

## Dzień 10 — Planner v0

* [ ] Zdefiniowanie prostego planera regułowego
* [ ] Opis mapowania inputu na plan
* [ ] Lista scenariuszy, które planner obsłuży
* [ ] Lista scenariuszy, które planner ma odrzucać

**Notatki:**

```text


```

---

## Dzień 11 — Walidacja planu

* [ ] Ustalenie reguł poprawności planu
* [ ] Spisanie błędów walidacji
* [ ] Ustalenie, kiedy plan trzeba odrzucić
* [ ] Ustalenie, kiedy plan można poprawić automatycznie

**Notatki:**

```text


```

---

## Dzień 12 — Wersja logiczna grafu

* [ ] Zapisanie grafu jako struktury danych
* [ ] Ustalenie, jak będą wyglądały węzły i krawędzie
* [ ] Ustalenie, jak będzie wyglądał wynik plannera
* [ ] Zapisanie przykładowego workflow w formie JSON

**Notatki:**

```text


```

---

## Dzień 13 — Wizualizacja planu

* [ ] Określenie, co dokładnie ma być widoczne na ekranie
* [ ] Lista elementów UI dla grafu
* [ ] Lista stanów tasków widocznych wizualnie
* [ ] Pierwszy szkic widoku workflow

**Notatki:**

```text


```

---

## Dzień 14 — Drugi milestone

* [ ] Planer potrafi wygenerować prosty workflow
* [ ] Workflow ma strukturę grafu
* [ ] Taski mają statusy
* [ ] Wizualizacja ma jasny zakres

**Kamień milowy:** plan da się już pokazać jako graf, nawet jeśli jeszcze nie jest „inteligentny”.

**Notatki:**

```text


```

---

## Dzień 15 — Historia uruchomień

* [ ] Określenie, co zapisuje się po każdym uruchomieniu
* [ ] Lista pól do historii
* [ ] Ustalenie, jak odtworzyć przebieg execution
* [ ] Definicja logów dla workflow

**Notatki:**

```text


```

---

## Dzień 16 — Pamięć systemu

* [ ] Zdefiniowanie, co system pamięta długoterminowo
* [ ] Podział na pamięć techniczną i pamięć użytkownika
* [ ] Ustalenie, co może być użyte ponownie przez planner
* [ ] Zapis przykładów pamięci

**Notatki:**

```text


```

---

## Dzień 17 — Lokalny model AI jako warstwa opcjonalna

* [ ] Zdefiniowanie roli lokalnego modelu
* [ ] Ustalenie, kiedy używać modelu, a kiedy reguł
* [ ] Lista typów zadań, które model ma wspierać
* [ ] Lista zadań, które nadal mają być deterministyczne

**Notatki:**

```text


```

---

## Dzień 18 — Integracja z lokalnym LLM stackiem

* [ ] Wybranie lokalnego modelu do planowania
* [ ] Określenie, jakie dane model dostaje
* [ ] Określenie, jaki format odpowiedzi model ma zwracać
* [ ] Ustalenie, jak walidować odpowiedź modelu

**Notatki:**

```text


```

---

## Dzień 19 — Fallback i kontrola jakości planu

* [ ] Określenie, co się dzieje po złej odpowiedzi modelu
* [ ] Ustalenie fallbacku do reguł
* [ ] Ustalenie, co uznajemy za plan niepoprawny
* [ ] Ustalenie, jak system informuje o błędzie

**Notatki:**

```text


```

---

## Dzień 20 — Pierwsza wersja „produktowa”

* [ ] Lista funkcji, które zostają w MVP
* [ ] Lista funkcji, które są odkładane
* [ ] Ocena, czy produkt nadaje się do pokazania komuś z zewnątrz
* [ ] Zapis braków i ryzyk

**Notatki:**

