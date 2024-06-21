#########
Using Git
#########


Contributing in GitHub
----------------------

There are three ways to contribute to the GitHub repository

1. Push commits directly to the main branch of hailegroup/nupylab. This
   requires your GitHub account to be listed as "Owner" in the hailegroup
   organization. You can log in with the Haile group account to change roles,
   or request a current owner to change your role for you.
2. Push to a new branch in hailegroup/nupylab for a new feature, then create
   a pull request to merge. This also requires Owner access.
3. Create a fork of nupylab in your own GitHub account first, branches for
   new features, and create a pull request anytime you want to update the
   main repository. The pull request will need to be approved by one of the
   organization Owners or by the Haile group account.

The second and third options are better and more stable long-term. Once the
repository is more mature and tests have been developed, pull requests allow
new commits to be evaluated before they are incorporated, reducing the chance
that a new commit breaks functionality in some way. New branches can be created
for each new instrument or station, or for adding support for a new Python or
dependency version, or some other feature. This way, new features can be
accepted or rejected individually rather than as a whole. It's common to have
an ongoing "develop" branch that individual feature branches merge into. Then
the develop branch can be merged into the main branch and the version updated.

In the short-term, before NUPyLab has wide adoption in the lab, option 1 is
okay and easier for quick development. Just be sure that individual commits are
relatively small so you don't lose too much work if one needs to be rolled
back. However, it is still preferable to push into the develop branch first,
then merge into the main branch.


Setting up access
-----------------

Whether you push to your own forked repository of nupylab or to the hailegroup
repository directly, being able to do so from your code editor or git client
makes things a lot easier. Good IDEs like PyCharm and VS Code have git
integrated, but you will need to `set up an SSH key pair`_ to push to and fetch
from remote (i.e. GitHub) repositories. Pull requests will still have to be
approved on GitHub, but everything else can be done locally.

Git can fee pretty complicated at first, but there are a lot of
`good tutorials`_ out there. Just be sure not to change files that aren't
related to the branch or feature you're working on, and that your commit
messages have clear descriptions.

.. _set up an SSH key pair: https://docs.github.com/en/authentication/connecting-to-github-with-ssh/generating-a-new-ssh-key-and-adding-it-to-the-ssh-agent
.. _good tutorials: https://www.youtube.com/watch?v=RGOj5yH7evk
