import { useTypesStore } from "../store/Types.js";
import { useTypesRender } from "../utils/useRender.js";
export var Int = props => {
  var _useTypesStore = useTypesStore(),
    _useTypesStore$Int = _useTypesStore.Int,
    Comp = _useTypesStore$Int === void 0 ? {} : _useTypesStore$Int;
  useTypesRender(Comp, props, 'Int');
  return null;
};
Int.displayName = 'JVR.Int';