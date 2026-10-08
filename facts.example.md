# Facts

Copy to facts.md and replace everything below with your own. Every line a tailored CV
may contain lives here, one checkable claim per line with an id. The model selects ids
and may not add anything; a separate check blocks any sentence no line supports.

Ids are grouped by letter. A letter followed by H is a header line the layout prints
above its section. cv_layout.py maps the letters to sections.

## Headline

- [H1] Junior Backend Developer, Java and SQL | tags: backend, java

## Summary lines

- [S5] Computer Science graduate who built a full-stack scheduling system with a REST API and a relational model. | tags: backend
- [S6] Before that, three years in retail management. | tags: retail
- [S3] Comfortable with Linux, SQL and Docker. | tags: linux, sql, docker

## Previous role

- [ZH1] Store Manager  |  Example Ltd  |  2021 – 2024 | tags: header
- [Z1] Ran a team of six and the store's weekly schedule. | tags: management

## Military service

- [VH1] Logistics NCO  |  IDF | tags: header
- [V1] Managed equipment inventory for a unit of eighty. | tags: logistics

## Main project

- [PH1] Library Loans  |  Java, Spring Boot, PostgreSQL  |  github.com/you/library-loans | tags: header
- [P1] Web system for tracking book loans and reservations. | tags: fullstack

## Second project

- [JH1] Job Agent  |  Python, SQLite | tags: header
- [J1] Built a pipeline that collects job postings and scores them with an LLM. | tags: python, llm

## Education

- [E1] B.Sc. in Software Engineering, Example University, 2025. | tags: education

## Rules for the writer, not facts

- [R1] The store role was management, not software. Never describe it as technical work. | tags: rule
- [R2] Summary lines: S5, S6, S3 in that order. | tags: rule
- [W1] A few sentences in your own words, so cover notes sound like you. Never printed. | tags: style

## Cover letter, fixed lines

The whole letter. The header names the company and the role, and {field} is filled in; L2 prints only when the company's field is new to you.

- [L1] Your opening line, in your own words. | tags: letter, opening
- [L2] A line using {field}, printed only when the company's field is new to you. | tags: letter, new-field
- [L3] A closing line in your own words. | tags: letter, closing
- [L4] Anything a recruiter needs to know, such as when you can start. | tags: letter, availability
- [L5] One line on how you studied or learn, in your own words. | tags: letter, study

## Problem stories, for form questions only

- [T1] A problem you worked through, in your own words, for form questions about one. | tags: story

## Motivation, not sent to the model

- [M1] I want a team where I keep learning from people better than me. | tags: motivation

## Skills

- [K1] Java | tags: java
- [K2] Python | tags: python
