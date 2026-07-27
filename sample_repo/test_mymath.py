import pytest
from mymath import add, divide, power

def test_add():
    assert add(2, 3) == 5

def test_divide():
    assert divide(10, 4) == 2.5

def test_divide_by_zero():
    with pytest.raises(ValueError):
        divide(1, 0)

def test_power():
    assert power(2, 10) == 1024
