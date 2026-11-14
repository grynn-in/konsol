---
title: Dimension Value permissions
audience: Everyone
section: Concepts
order: 140
---
A site declares its own dimensions (Dimension records), for example `region`. Konsol ships none. A dimension can decide who may write budget figures on each of its values.

## Gate budget writes by a dimension

1. On a Published Dimension with **Include in Budget** ticked, set **Permission DocType** to **Dimension Value**.
2. Give each user a **User Permission** with Allow = Dimension Value and For Value = `<dimension>:<value>`, for example `region:EU`.

A Dimension Value is one value of one declared dimension, named `<dimension>:<value>`. A grant allows that value on that dimension only; a grant on another dimension never allows it.

## What the gate does

- It checks budget figures written through konsol's budget save methods and `=K.EPMSAVE` in Excel. A write of a value the user holds no grant for is refused with "Not permitted to write …".
- It also checks a Budget Entry edited in its own Desk form: the entry judges its lines through the same rules.
- Once a dimension is gated, a user with no grant on it can write no value of it.
- Administrator and System Manager are not restricted.
- It does not restrict reading. What a user reads is limited by the entities they are assigned.
- A dimension with a blank Permission DocType is descriptive only: it gates nothing.

The same save methods also check the entity and the account, each once its own permission is set up.
