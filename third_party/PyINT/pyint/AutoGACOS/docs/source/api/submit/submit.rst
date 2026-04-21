Requests Submitter
==================

The GACOS limits the number of acquisition requests that can be submitted at a time. 
This is to prevent the system from being overloaded. But, if you have a large number
of acquisition requests to submit, you can submit them in batches. We designed a
:class:`gacos.Submitter` class to help you submit the requests in batches. The 
acquisitions will be split into batches which not exceed the 20 acquisitions per 
request.

.. autoclass:: gacos.Submitter
   :members:
   :undoc-members:
   :member-order: bysource
   :show-inheritance:
