from pydantic import BaseModel, Field
from typing import Optional
import math
import json


class ExtendedBaseModel(BaseModel):
    # Override JSON encoders to handle NaN and infinity
    class Config:
        json_encoders = {
            float: lambda x: (
                'NaN' if math.isnan(x) else
                'Infinity' if x == float('inf') else
                '-Infinity' if x == -float('inf') else x
            )
        }

    @classmethod
    def custom_json_decoder(cls, v):
        # Decode 'NaN', 'Infinity', and '-Infinity' strings back to float values
        if isinstance(v, str):
            if v == 'NaN':
                return float('nan')
            elif v == 'Infinity':
                return float('inf')
            elif v == '-Infinity':
                return -float('inf')
        return v

    @classmethod
    def parse_obj(cls, obj):
        # Recursively apply the custom decoder to handle nested data structures
        obj = json.loads(json.dumps(obj), object_hook=cls.custom_json_decoder)
        return super().parse_obj(obj)

# # Example usage
# class MyModel(CustomBaseModel):
#     value: Optional[float] = Field(None, description="A float that may be NaN or infinity")
#
# # Encoding Example
# m = MyModel(value=float('inf'))
# print(m.json())  # Output: {"value": "Infinity"}
#
# # Decoding Example
# data = '{"value": "NaN"}'
# m = MyModel.parse_raw(data)
# print(m.value)  # Output: nan
