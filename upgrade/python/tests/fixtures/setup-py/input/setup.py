from setuptools import setup as configure

DESCRIPTION = "daytona-sdk==0.224.0"
REQUIRES = [
    'httpx>=0.27',
    'daytona-sdk==0.224.0; python_version >= "3.9"',  # client
]
configure(
    name='sandbox-workflow',
    description=DESCRIPTION,
    install_requires=REQUIRES,
    extras_require={'daytona-sdk': ['daytona_sdk[otel]==0.224.0']},
)
print('daytona-sdk==0.224.0')
