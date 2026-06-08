import { useTypesStore } from "../store/Types.js";
import { useTypesRender } from "../utils/useRender.js";
export var Null = props => {
  var _useTypesStore = useTypesStore(),
    _useTypesStore$Null = _useTypesStore.Null,
    Comp = _useTypesStore$Null === void 0 ? {} : _useTypesStore$Null;
  useTypesRender(Comp, props, 'Null');
  return null;
};
Null.displayName = 'JVR.Null';