Filter
======

Parse and evaluate Haystack filter expressions. ``parse()`` produces an AST,
``evaluate()`` tests against dicts, ``evaluate_grid()`` filters grids.

.. automodule:: hs_py.filter
   :no-members:

AST
---

Filter AST nodes: Has, Missing, Cmp, And, Or, Path.

.. automodule:: hs_py.filter.ast
   :members:

Lexer
-----

Tokenizer for Haystack filter expressions.

.. automodule:: hs_py.filter.lexer
   :members:

Parser
------

Recursive descent parser producing filter AST.

.. automodule:: hs_py.filter.parser
   :members:

Evaluator
---------

Evaluate filter AST against dicts and grids.

.. automodule:: hs_py.filter.eval
   :members:
