import { useTypesStore } from "../store/Types.js";
import { useTypesRender } from "../utils/useRender.js";
export var Bigint = props => {
  var _useTypesStore = useTypesStore(),
    _useTypesStore$Bigint = _useTypesStore.Bigint,
    Comp = _useTypesStore$Bigint === void 0 ? {} : _useTypesStore$Bigint;
  useTypesRender(Comp, props, 'Bigint');
  return null;
};
Bigint.displayName = 'JVR.Bigint';