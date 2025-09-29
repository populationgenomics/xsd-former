import types


def test_generate_protobufs(book_pb2: types.ModuleType) -> None:
  assert hasattr(book_pb2, "Book")
  assert hasattr(book_pb2, "Author")
  assert hasattr(book_pb2.Role, "ROLE_AUTHOR")
